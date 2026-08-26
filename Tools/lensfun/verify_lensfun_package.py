#!/usr/bin/env python3
"""Validate the checked-in Celvion Stack Lensfun data and legal bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PINNED_COMMIT = "5bfb8d8cb151a3a4068219cfc798f63d0641ff19"
PINNED_DATABASE_MANIFEST_SHA256 = (
    "0254470a85dfeba1931330988a4e46d5738e18d3b8ac54546fd65bc1700d7a25"
)
PINNED_LICENSE_SHA256 = (
    "3f941b3b89cf7b8370ceb83cc76d2120d471b58735d8ca60238a751a48d7f72f"
)
EXPECTED_COUNTS = {
    "mounts": 52,
    "cameras": 1051,
    "lenses": 1563,
    "lensesWithDistortion": 1521,
    "distortionSamples": 6431,
}
EXPECTED_MODEL_COUNTS = {"poly3": 873, "poly5": 5, "ptlens": 5553}
TERM_KEYS = {
    "poly3": {"k1"},
    "poly5": {"k1", "k2"},
    "ptlens": {"a", "b", "c"},
}


class ValidationError(RuntimeError):
    """Raised when the distributable package is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def require_number(value: Any, field: str, *, positive: bool = False) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field} is not numeric")
    result = float(value)
    require(math.isfinite(result), f"{field} is not finite")
    if positive:
        require(result > 0, f"{field} is not positive")
    return result


def verify_checksums(directory: Path) -> None:
    checksum_path = directory / "SHA256SUMS"
    require(checksum_path.is_file(), "missing SHA256SUMS")
    seen: set[str] = set()
    for line_number, line in enumerate(checksum_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        pieces = line.split("  ", 1)
        require(len(pieces) == 2, f"invalid SHA256SUMS line {line_number}")
        expected, filename = pieces
        require(len(expected) == 64, f"invalid digest on SHA256SUMS line {line_number}")
        require(filename not in seen, f"duplicate checksum entry: {filename}")
        seen.add(filename)
        path = directory / filename
        require(path.is_file(), f"missing checksummed file: {filename}")
        require(sha256_file(path) == expected, f"checksum mismatch: {filename}")
    require(
        seen == {
            "LICENSE-CC-BY-SA-3.0.txt", "NOTICE.md", "README.md",
            "lensfun-distortion-v1.json",
        },
        f"unexpected checksum inventory: {sorted(seen)}",
    )
    require(
        sha256_file(directory / "LICENSE-CC-BY-SA-3.0.txt") == PINNED_LICENSE_SHA256,
        "the complete upstream CC-BY-SA-3.0 license text changed",
    )


def verify_source_manifest(source: dict[str, Any]) -> None:
    require(source.get("commit") == PINNED_COMMIT, "wrong Lensfun source commit")
    require(source.get("databaseVersion") == 2, "wrong Lensfun database version")
    files = source.get("files")
    require(isinstance(files, list) and len(files) == 56, "source manifest must contain 56 XML files")
    combined = hashlib.sha256()
    paths: list[str] = []
    for index, item in enumerate(files):
        require(isinstance(item, dict), f"source file {index} is not an object")
        path = item.get("path")
        digest = item.get("sha256")
        byte_count = item.get("bytes")
        require(isinstance(path, str) and path.startswith("data/db/") and path.endswith(".xml"), f"invalid source path {path!r}")
        require(isinstance(digest, str) and len(digest) == 64, f"invalid source digest for {path}")
        require(isinstance(byte_count, int) and byte_count > 0, f"invalid source size for {path}")
        paths.append(path)
        combined.update(path.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\n")
    require(paths == sorted(paths), "source manifest is not sorted")
    manifest_hash = combined.hexdigest()
    require(manifest_hash == PINNED_DATABASE_MANIFEST_SHA256, "source manifest digest mismatch")
    require(source.get("databaseManifestSHA256") == manifest_hash, "declared source manifest digest mismatch")


def verify_multilingual(values: Any, field: str) -> None:
    require(isinstance(values, list) and values, f"{field} is empty")
    for index, item in enumerate(values):
        require(isinstance(item, dict), f"{field}[{index}] is not an object")
        require(set(item) == {"language", "value"}, f"unexpected keys in {field}[{index}]")
        require(isinstance(item["language"], str) and item["language"], f"empty language in {field}[{index}]")
        require(isinstance(item["value"], str) and item["value"], f"empty value in {field}[{index}]")


def verify_ratio(ratio: Any, field: str) -> None:
    require(isinstance(ratio, dict) and set(ratio) == {"width", "height"}, f"invalid {field}")
    require(isinstance(ratio["width"], int) and ratio["width"] > 0, f"invalid {field}.width")
    require(isinstance(ratio["height"], int) and ratio["height"] > 0, f"invalid {field}.height")


def expected_real_focal(sample: dict[str, Any]) -> float:
    focal = float(sample["focal"])
    terms = sample["terms"]
    if sample["model"] == "ptlens":
        return focal * (1 - terms["a"] - terms["b"] - terms["c"])
    if sample["model"] == "poly3":
        return focal * (1 - terms["k1"])
    return focal


def verify_package(package: dict[str, Any]) -> None:
    require(package.get("schemaVersion") == 1, "unsupported package schema")
    require(package.get("packageIdentifier") == "com.celvionstack.lensfun-distortion.v1", "wrong package identifier")
    license_info = package.get("license")
    require(isinstance(license_info, dict), "missing license metadata")
    require(license_info.get("spdxIdentifier") == "CC-BY-SA-3.0", "wrong data license")
    require(license_info.get("licenseFile") == "LICENSE-CC-BY-SA-3.0.txt", "wrong license file")
    derivative = package.get("derivative")
    require(isinstance(derivative, dict), "missing derivative notice")
    changes = derivative.get("changes")
    require(isinstance(changes, list) and len(changes) >= 6, "incomplete modification notice")
    require(any("No distortion model" in change for change in changes), "missing no-approximation declaration")
    semantics = package.get("coordinateSemantics")
    require(isinstance(semantics, dict), "missing coordinate semantics")
    require("1 - a - b - c" in semantics.get("ptlens", ""), "PTLens constant term was not preserved")
    require("1 - k1" in semantics.get("poly3", ""), "Poly3 constant term was not preserved")
    verify_source_manifest(package.get("source", {}))

    mounts = package.get("mounts")
    cameras = package.get("cameras")
    lenses = package.get("lenses")
    require(isinstance(mounts, list), "mounts is not a list")
    require(isinstance(cameras, list), "cameras is not a list")
    require(isinstance(lenses, list), "lenses is not a list")
    for index, mount in enumerate(mounts):
        verify_multilingual(mount.get("names"), f"mounts[{index}].names")
    for index, camera in enumerate(cameras):
        verify_multilingual(camera.get("makers"), f"cameras[{index}].makers")
        verify_multilingual(camera.get("models"), f"cameras[{index}].models")
        require_number(camera.get("cropFactor"), f"cameras[{index}].cropFactor", positive=True)
        verify_ratio(camera.get("aspectRatio"), f"cameras[{index}].aspectRatio")

    ids: set[str] = set()
    model_counter: Counter[str] = Counter()
    lenses_with_distortion = 0
    sample_count = 0
    for lens_index, lens in enumerate(lenses):
        identifier = lens.get("id")
        require(isinstance(identifier, str) and len(identifier) == 24, f"invalid lens id at {lens_index}")
        require(identifier not in ids, f"duplicate lens id: {identifier}")
        ids.add(identifier)
        verify_multilingual(lens.get("makers"), f"lenses[{lens_index}].makers")
        verify_multilingual(lens.get("models"), f"lenses[{lens_index}].models")
        require(isinstance(lens.get("mounts"), list) and lens["mounts"], f"lens {identifier} has no mount")
        require_number(lens.get("cropFactor"), f"lens {identifier} cropFactor", positive=True)
        verify_ratio(lens.get("aspectRatio"), f"lens {identifier} aspectRatio")
        center = lens.get("center")
        require(isinstance(center, dict) and set(center) == {"x", "y"}, f"invalid center for {identifier}")
        require_number(center["x"], f"lens {identifier} center.x")
        require_number(center["y"], f"lens {identifier} center.y")
        samples = lens.get("distortions")
        require(isinstance(samples, list), f"invalid distortions for {identifier}")
        lenses_with_distortion += bool(samples)
        for sample_index, sample in enumerate(samples):
            prefix = f"lens {identifier} sample {sample_index}"
            model = sample.get("model")
            require(model in TERM_KEYS, f"unsupported model in {prefix}: {model!r}")
            terms = sample.get("terms")
            require(isinstance(terms, dict) and set(terms) == TERM_KEYS[model], f"wrong term set in {prefix}")
            for term, value in terms.items():
                require_number(value, f"{prefix}.{term}")
            require_number(sample.get("focal"), f"{prefix}.focal", positive=True)
            real_focal = require_number(sample.get("realFocal"), f"{prefix}.realFocal", positive=True)
            require(isinstance(sample.get("realFocalMeasured"), bool), f"invalid measured flag in {prefix}")
            require_number(sample.get("calibrationCropFactor"), f"{prefix}.calibrationCropFactor", positive=True)
            verify_ratio(sample.get("calibrationAspectRatio"), f"{prefix}.calibrationAspectRatio")
            if not sample["realFocalMeasured"]:
                require(
                    math.isclose(real_focal, expected_real_focal(sample), rel_tol=1e-13, abs_tol=1e-13),
                    f"incorrect derived real focal length in {prefix}",
                )
            model_counter[model] += 1
            sample_count += 1

    counts = {
        "mounts": len(mounts),
        "cameras": len(cameras),
        "lenses": len(lenses),
        "lensesWithDistortion": lenses_with_distortion,
        "distortionSamples": sample_count,
    }
    require(counts == EXPECTED_COUNTS, f"record counts changed: {counts}")
    require(package.get("counts") == EXPECTED_COUNTS, "declared record counts mismatch")
    require(dict(sorted(model_counter.items())) == EXPECTED_MODEL_COUNTS, "model counts mismatch")
    require(package.get("distortionModelCounts") == EXPECTED_MODEL_COUNTS, "declared model counts mismatch")


def parse_arguments(argv: Iterable[str]) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    standalone_package = default_root
    app_package = default_root / "LandscapeStacker" / "Resources" / "Lensfun"
    default_package = (
        standalone_package
        if (standalone_package / "lensfun-distortion-v1.json").is_file()
        else app_package
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=default_package,
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    arguments = parse_arguments(argv)
    directory = arguments.package_dir.resolve()
    try:
        verify_checksums(directory)
        package_path = directory / "lensfun-distortion-v1.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        require(isinstance(package, dict), "package root is not an object")
        verify_package(package)
        print(
            f"verified {package_path} "
            f"({EXPECTED_COUNTS['lenses']} lenses, "
            f"{EXPECTED_COUNTS['distortionSamples']} distortion samples, "
            "exact Lensfun model semantics retained)"
        )
        return 0
    except (ValidationError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
