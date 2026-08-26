#!/usr/bin/env python3
"""Build Celvion Stack's deterministic Lensfun distortion data package.

The converter intentionally reads Lensfun's XML database directly and does
not build or link the Lensfun C++ library.  It preserves the three source
distortion models and all coordinate-system inputs required to implement their
semantics; it never approximates PTLens or Poly3 as Brown-Conrady.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PINNED_COMMIT = "5bfb8d8cb151a3a4068219cfc798f63d0641ff19"
PINNED_DATABASE_MANIFEST_SHA256 = (
    "0254470a85dfeba1931330988a4e46d5738e18d3b8ac54546fd65bc1700d7a25"
)
PACKAGE_IDENTIFIER = "com.celvionstack.lensfun-distortion.v1"
SUPPORTED_MODELS = {"poly3", "poly5", "ptlens"}
EXPECTED_COUNTS = {
    "mounts": 52,
    "cameras": 1051,
    "lenses": 1563,
    "lensesWithDistortion": 1521,
    "distortionSamples": 6431,
}
EXPECTED_MODEL_COUNTS = {"poly3": 873, "poly5": 5, "ptlens": 5553}


class ConversionError(RuntimeError):
    """Raised when a source checkout cannot be converted without data loss."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text(element: ET.Element | None, *, required: bool = False) -> str | None:
    if element is None:
        if required:
            raise ConversionError("missing required text element")
        return None
    value = " ".join("".join(element.itertext()).split())
    if not value:
        if required:
            raise ConversionError(f"empty required <{element.tag}> element")
        return None
    return value


def finite_number(value: str | None, *, field: str, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise ConversionError(f"missing required numeric field {field}")
        return None
    try:
        result = float(value)
    except ValueError as error:
        raise ConversionError(f"invalid {field}: {value!r}") from error
    if not math.isfinite(result):
        raise ConversionError(f"non-finite {field}: {value!r}")
    return result


def localized_values(parent: ET.Element, tag: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for element in parent.findall(tag):
        value = normalized_text(element, required=True)
        assert value is not None
        language = element.attrib.get("lang", "und").strip() or "und"
        unknown = set(element.attrib) - {"lang"}
        if unknown:
            raise ConversionError(f"unsupported attributes on <{tag}>: {sorted(unknown)}")
        values.append({"language": language, "value": value})
    if not values:
        raise ConversionError(f"missing <{tag}> value")
    return values


def primary_value(values: list[dict[str, str]]) -> str:
    for preferred in ("und", "en"):
        for item in values:
            if item["language"] == preferred:
                return item["value"]
    return values[0]["value"]


def aspect_ratio(value: str | None, *, default: str = "3:2") -> dict[str, int]:
    source = value or default
    pieces = source.split(":")
    if len(pieces) != 2:
        raise ConversionError(f"unsupported aspect ratio: {source!r}")
    try:
        width, height = (int(piece) for piece in pieces)
    except ValueError as error:
        raise ConversionError(f"invalid aspect ratio: {source!r}") from error
    if width <= 0 or height <= 0:
        raise ConversionError(f"non-positive aspect ratio: {source!r}")
    return {"width": width, "height": height}


def optional_range(element: ET.Element | None, *, name: str) -> dict[str, float] | None:
    if element is None:
        return None
    unknown = set(element.attrib) - {"min", "max", "value"}
    if unknown:
        raise ConversionError(f"unsupported <{name}> attributes: {sorted(unknown)}")
    if "value" in element.attrib:
        if "min" in element.attrib or "max" in element.attrib:
            raise ConversionError(f"<{name}> mixes value with min/max")
        value = finite_number(element.attrib["value"], field=f"{name}.value", required=True)
        assert value is not None
        return {"minimum": value, "maximum": value}
    minimum = finite_number(element.attrib.get("min"), field=f"{name}.min")
    maximum = finite_number(element.attrib.get("max"), field=f"{name}.max")
    if minimum is None and maximum is None:
        raise ConversionError(f"empty <{name}> range")
    if minimum is None:
        minimum = maximum
    if maximum is None:
        maximum = minimum
    assert minimum is not None and maximum is not None
    if minimum <= 0 or maximum < minimum:
        raise ConversionError(f"invalid <{name}> range: {minimum}...{maximum}")
    return {"minimum": minimum, "maximum": maximum}


def stable_identifier(*parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def source_manifest(lensfun_root: Path) -> tuple[list[dict[str, Any]], str]:
    xml_paths = sorted((lensfun_root / "data" / "db").glob("*.xml"))
    if not xml_paths:
        raise ConversionError(f"no Lensfun XML files under {lensfun_root / 'data/db'}")
    manifest: list[dict[str, Any]] = []
    combined = hashlib.sha256()
    for path in xml_paths:
        relative = path.relative_to(lensfun_root).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        manifest.append({"path": relative, "sha256": digest, "bytes": size})
        combined.update(relative.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\n")
    return manifest, combined.hexdigest()


def verify_checkout_identity(lensfun_root: Path, declared_commit: str) -> None:
    if declared_commit != PINNED_COMMIT:
        raise ConversionError(
            f"source commit must be the pinned Lensfun revision {PINNED_COMMIT}; "
            f"got {declared_commit}"
        )
    git_dir = lensfun_root / ".git"
    if git_dir.exists():
        try:
            actual = subprocess.check_output(
                ["git", "-C", str(lensfun_root), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.STDOUT,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ConversionError(f"could not verify Lensfun Git revision: {error}") from error
        if actual != PINNED_COMMIT:
            raise ConversionError(
                f"Lensfun checkout is {actual}; expected pinned revision {PINNED_COMMIT}"
            )


def parse_mount(element: ET.Element, source_path: str, ordinal: int) -> dict[str, Any]:
    unknown_children = {child.tag for child in element} - {"name", "compat"}
    if unknown_children:
        raise ConversionError(f"unsupported mount fields: {sorted(unknown_children)}")
    names = localized_values(element, "name")
    compatible = [
        value
        for child in element.findall("compat")
        if (value := normalized_text(child, required=True)) is not None
    ]
    return {
        "id": stable_identifier("mount", source_path, str(ordinal), primary_value(names)),
        "names": names,
        "compatibleMounts": compatible,
        "source": {"file": source_path, "ordinal": ordinal},
    }


def parse_camera(element: ET.Element, source_path: str, ordinal: int) -> dict[str, Any]:
    unknown_children = {child.tag for child in element} - {
        "maker", "model", "variant", "mount", "cropfactor", "aspect-ratio"
    }
    if unknown_children:
        raise ConversionError(f"unsupported camera fields: {sorted(unknown_children)}")
    makers = localized_values(element, "maker")
    models = localized_values(element, "model")
    mount = normalized_text(element.find("mount"), required=True)
    crop_factor = finite_number(
        normalized_text(element.find("cropfactor"), required=True),
        field="camera.cropfactor",
        required=True,
    )
    assert mount is not None and crop_factor is not None
    if crop_factor <= 0:
        raise ConversionError("camera crop factor must be positive")
    variant = normalized_text(element.find("variant"))
    return {
        "id": stable_identifier(
            "camera", source_path, str(ordinal), primary_value(makers),
            primary_value(models), variant or "", mount,
        ),
        "makers": makers,
        "models": models,
        "variant": variant,
        "mount": mount,
        "cropFactor": crop_factor,
        "aspectRatio": aspect_ratio(normalized_text(element.find("aspect-ratio"))),
        "source": {"file": source_path, "ordinal": ordinal},
    }


def resolved_real_focal(model: str, focal: float, terms: dict[str, float]) -> float:
    if model == "ptlens":
        return focal * (1 - terms["a"] - terms["b"] - terms["c"])
    if model == "poly3":
        return focal * (1 - terms["k1"])
    return focal


def parse_distortion(
    element: ET.Element,
    *,
    calibration_crop_factor: float,
    calibration_aspect_ratio: dict[str, int],
) -> dict[str, Any]:
    model = element.attrib.get("model")
    if model not in SUPPORTED_MODELS:
        raise ConversionError(f"unsupported distortion model: {model!r}")
    focal = finite_number(element.attrib.get("focal"), field="distortion.focal", required=True)
    assert focal is not None
    if focal <= 0:
        raise ConversionError("distortion focal length must be positive")

    expected_terms = {
        "poly3": ("k1",),
        "poly5": ("k1", "k2"),
        "ptlens": ("a", "b", "c"),
    }[model]
    allowed = {"model", "focal", "real-focal", *expected_terms}
    unknown = set(element.attrib) - allowed
    if unknown:
        raise ConversionError(
            f"unsupported attributes for {model} distortion: {sorted(unknown)}"
        )
    terms = {
        key: finite_number(element.attrib.get(key, "0"), field=f"distortion.{key}", required=True)
        for key in expected_terms
    }
    assert all(value is not None for value in terms.values())
    numeric_terms = {key: float(value) for key, value in terms.items()}
    explicit_real_focal = finite_number(
        element.attrib.get("real-focal"), field="distortion.real-focal"
    )
    real_focal = explicit_real_focal or resolved_real_focal(model, focal, numeric_terms)
    if real_focal <= 0:
        raise ConversionError("resolved real focal length must be positive")

    return {
        "model": model,
        "focal": focal,
        "realFocal": real_focal,
        "realFocalMeasured": explicit_real_focal is not None,
        "terms": numeric_terms,
        "calibrationCropFactor": calibration_crop_factor,
        "calibrationAspectRatio": calibration_aspect_ratio,
    }


def parse_lens(element: ET.Element, source_path: str, ordinal: int) -> dict[str, Any]:
    unknown_children = {child.tag for child in element} - {
        "maker", "model", "mount", "focal", "aperture", "type", "center",
        "cropfactor", "aspect-ratio", "calibration",
    }
    if unknown_children:
        raise ConversionError(f"unsupported lens fields: {sorted(unknown_children)}")
    makers = localized_values(element, "maker")
    models = localized_values(element, "model")
    mounts = [
        value
        for child in element.findall("mount")
        if (value := normalized_text(child, required=True)) is not None
    ]
    if not mounts:
        raise ConversionError("lens has no mount")
    crop_factor = finite_number(
        normalized_text(element.find("cropfactor"), required=True),
        field="lens.cropfactor",
        required=True,
    )
    assert crop_factor is not None
    if crop_factor <= 0:
        raise ConversionError("lens crop factor must be positive")
    lens_aspect_ratio = aspect_ratio(normalized_text(element.find("aspect-ratio")))
    projection = normalized_text(element.find("type")) or "rectilinear"

    center_element = element.find("center")
    center_x = 0.0
    center_y = 0.0
    if center_element is not None:
        unknown = set(center_element.attrib) - {"x", "y"}
        if unknown:
            raise ConversionError(f"unsupported lens center attributes: {sorted(unknown)}")
        center_x = finite_number(center_element.attrib.get("x", "0"), field="center.x", required=True) or 0
        center_y = finite_number(center_element.attrib.get("y", "0"), field="center.y", required=True) or 0

    distortions: list[dict[str, Any]] = []
    for calibration in element.findall("calibration"):
        unknown_calibration_children = {child.tag for child in calibration} - {
            "distortion", "tca", "vignetting", "crop", "field_of_view"
        }
        if unknown_calibration_children:
            raise ConversionError(
                f"unsupported calibration fields: {sorted(unknown_calibration_children)}"
            )
        unknown_attributes = set(calibration.attrib) - {"cropfactor", "aspect-ratio"}
        if unknown_attributes:
            raise ConversionError(
                f"unsupported calibration attributes: {sorted(unknown_attributes)}"
            )
        calibration_crop = finite_number(
            calibration.attrib.get("cropfactor", str(crop_factor)),
            field="calibration.cropfactor",
            required=True,
        )
        assert calibration_crop is not None
        calibration_aspect = aspect_ratio(
            calibration.attrib.get("aspect-ratio"),
            default=f"{lens_aspect_ratio['width']}:{lens_aspect_ratio['height']}",
        )
        distortions.extend(
            parse_distortion(
                child,
                calibration_crop_factor=calibration_crop,
                calibration_aspect_ratio=calibration_aspect,
            )
            for child in calibration.findall("distortion")
        )

    return {
        "id": stable_identifier(
            "lens", source_path, str(ordinal), primary_value(makers),
            primary_value(models), "\x1e".join(mounts), str(crop_factor),
        ),
        "makers": makers,
        "models": models,
        "mounts": mounts,
        "focalRange": optional_range(element.find("focal"), name="focal"),
        "apertureRange": optional_range(element.find("aperture"), name="aperture"),
        "projection": projection,
        "cropFactor": crop_factor,
        "aspectRatio": lens_aspect_ratio,
        "center": {"x": center_x, "y": center_y},
        "distortions": distortions,
        "source": {"file": source_path, "ordinal": ordinal},
    }


def parse_database(lensfun_root: Path, manifest: list[dict[str, Any]]) -> dict[str, Any]:
    mounts: list[dict[str, Any]] = []
    cameras: list[dict[str, Any]] = []
    lenses: list[dict[str, Any]] = []
    database_versions: set[int] = set()

    for item in manifest:
        source_path = item["path"]
        root = ET.parse(lensfun_root / source_path).getroot()
        if root.tag != "lensdatabase":
            raise ConversionError(f"unexpected root <{root.tag}> in {source_path}")
        try:
            database_versions.add(int(root.attrib["version"]))
        except (KeyError, ValueError) as error:
            raise ConversionError(f"invalid database version in {source_path}") from error
        unknown_root_children = {child.tag for child in root} - {"mount", "camera", "lens"}
        if unknown_root_children:
            raise ConversionError(
                f"unsupported database fields in {source_path}: {sorted(unknown_root_children)}"
            )
        mounts.extend(
            parse_mount(element, source_path, ordinal)
            for ordinal, element in enumerate(root.findall("mount"), start=1)
        )
        cameras.extend(
            parse_camera(element, source_path, ordinal)
            for ordinal, element in enumerate(root.findall("camera"), start=1)
        )
        lenses.extend(
            parse_lens(element, source_path, ordinal)
            for ordinal, element in enumerate(root.findall("lens"), start=1)
        )

    if database_versions != {2}:
        raise ConversionError(f"expected Lensfun database version 2; got {sorted(database_versions)}")
    return {"mounts": mounts, "cameras": cameras, "lenses": lenses}


def validate_counts(data: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    lenses = data["lenses"]
    samples = [sample for lens in lenses for sample in lens["distortions"]]
    counts = {
        "mounts": len(data["mounts"]),
        "cameras": len(data["cameras"]),
        "lenses": len(lenses),
        "lensesWithDistortion": sum(bool(lens["distortions"]) for lens in lenses),
        "distortionSamples": len(samples),
    }
    model_counts = dict(sorted(Counter(sample["model"] for sample in samples).items()))
    if counts != EXPECTED_COUNTS:
        raise ConversionError(f"pinned source record counts changed: {counts} != {EXPECTED_COUNTS}")
    if model_counts != EXPECTED_MODEL_COUNTS:
        raise ConversionError(
            f"pinned source distortion models changed: {model_counts} != {EXPECTED_MODEL_COUNTS}"
        )
    if len({item["id"] for item in data["mounts"]}) != len(data["mounts"]):
        raise ConversionError("duplicate generated mount identifier")
    if len({item["id"] for item in data["cameras"]}) != len(data["cameras"]):
        raise ConversionError("duplicate generated camera identifier")
    if len({item["id"] for item in lenses}) != len(lenses):
        raise ConversionError("duplicate generated lens identifier")
    return counts, model_counts


def build_package(lensfun_root: Path, declared_commit: str) -> dict[str, Any]:
    verify_checkout_identity(lensfun_root, declared_commit)
    manifest, manifest_hash = source_manifest(lensfun_root)
    if manifest_hash != PINNED_DATABASE_MANIFEST_SHA256:
        raise ConversionError(
            "Lensfun XML manifest does not match the pinned revision: "
            f"{manifest_hash} != {PINNED_DATABASE_MANIFEST_SHA256}"
        )
    data = parse_database(lensfun_root, manifest)
    counts, model_counts = validate_counts(data)
    return {
        "schemaVersion": 1,
        "packageIdentifier": PACKAGE_IDENTIFIER,
        "license": {
            "spdxIdentifier": "CC-BY-SA-3.0",
            "name": "Creative Commons Attribution-ShareAlike 3.0 Unported",
            "licenseFile": "LICENSE-CC-BY-SA-3.0.txt",
            "attribution": "Lensfun database contributors; original PTLens data by Tom Niemann",
            "sourceProjectURL": "https://github.com/lensfun/lensfun",
        },
        "source": {
            "project": "Lensfun",
            "repository": "https://github.com/lensfun/lensfun.git",
            "commit": PINNED_COMMIT,
            "commitURL": f"https://github.com/lensfun/lensfun/commit/{PINNED_COMMIT}",
            "databaseVersion": 2,
            "databaseManifestSHA256": manifest_hash,
            "files": manifest,
        },
        "derivative": {
            "name": "Celvion Stack Lensfun Distortion Data v1",
            "conversion": "Build-time XML-to-JSON structural conversion",
            "changes": [
                "Mount, camera, lens identity, projection, crop, aspect, center, and geometric-distortion records were retained.",
                "Localized XML strings were represented as ordered language/value arrays and stable derived identifiers were added.",
                "Lensfun XML defaults were made explicit; absent real-focal values were resolved with Lensfun's model-specific rules and marked as unmeasured.",
                "TCA, vignetting, crop-boundary, and field-of-view calibration records were omitted because Celvion Stack v1 consumes geometric distortion only.",
                "Source file paths, record ordinals, byte sizes, and SHA-256 values were added for auditability.",
                "No distortion model was fitted, approximated, or converted to Brown-Conrady.",
            ],
            "separateFromApplicationCode": True,
        },
        "coordinateSemantics": {
            "calibrationSystem": "Lensfun Hugin/PanoTools",
            "origin": "Lensfun geometric lens center",
            "axes": {"x": "positive right", "y": "positive down"},
            "calibrationRadiusUnit": "half of the image long edge; r = 1 at the middle of that edge",
            "centerFields": "Lensfun relative lens-center offsets; zero means geometric image center",
            "poly3": "rd = ru * (1 - k1 + k1 * ru^2)",
            "poly5": "rd = ru * (1 + k1 * ru^2 + k2 * ru^4)",
            "ptlens": "rd = ru * (a * ru^3 + b * ru^2 + c * ru + 1 - a - b - c)",
            "interpolation": "Model-homogeneous focal interpolation must follow Lensfun semantics; never mix model types.",
            "reference": f"https://github.com/lensfun/lensfun/blob/{PINNED_COMMIT}/libs/lensfun/modifier.cpp",
        },
        "counts": counts,
        "distortionModelCounts": model_counts,
        **data,
    }


def encode_package(package: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            package,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def parse_arguments(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lensfun-root",
        type=Path,
        required=True,
        help="Lensfun checkout/archive root containing data/db/*.xml",
    )
    parser.add_argument("--output", type=Path, required=True, help="output JSON path")
    parser.add_argument(
        "--source-commit",
        default=PINNED_COMMIT,
        help="declared revision for a source archive without .git",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the existing output is byte-for-byte reproducible",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    arguments = parse_arguments(argv)
    try:
        package = build_package(arguments.lensfun_root.resolve(), arguments.source_commit)
        encoded = encode_package(package)
        if arguments.check:
            if not arguments.output.is_file():
                raise ConversionError(f"missing generated package: {arguments.output}")
            existing = arguments.output.read_bytes()
            if existing != encoded:
                raise ConversionError(
                    f"generated package is stale: {arguments.output}; rerun without --check"
                )
            action = "verified"
        else:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_bytes(encoded)
            action = "wrote"
        print(
            f"{action} {arguments.output} ({len(encoded)} bytes; "
            f"{package['counts']['lenses']} lenses; "
            f"{package['counts']['distortionSamples']} distortion samples; "
            f"sha256={hashlib.sha256(encoded).hexdigest()})"
        )
        return 0
    except (ConversionError, ET.ParseError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
