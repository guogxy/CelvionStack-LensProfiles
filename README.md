# Celvion Stack Lensfun Distortion Data v1

This directory is the redistributable, offline lens-distortion data package
used by Celvion Stack. It is deliberately kept separate from the app's source
code so the share-alike terms apply clearly to the derived data package, not to
independent application code.

## Contents

- `lensfun-distortion-v1.json` — deterministic derived data package.
- `NOTICE.md` — attribution, source, and modification notice.
- `LICENSE-CC-BY-SA-3.0.txt` — complete license text.
- `SHA256SUMS` — integrity hashes for the distributable files.

The package contains 52 mount definitions, 1,051 camera definitions, 1,563
lens definitions, and 6,431 geometric-distortion samples. Of those lens
definitions, 1,521 contain at least one distortion sample.

## Source and attribution

The source is the Lensfun database at the fixed commit:

`5bfb8d8cb151a3a4068219cfc798f63d0641ff19`

- Project: <https://github.com/lensfun/lensfun>
- Exact revision: <https://github.com/lensfun/lensfun/commit/5bfb8d8cb151a3a4068219cfc798f63d0641ff19>
- Original authorship: Lensfun database contributors; the Lensfun project also
  credits Tom Niemann for the original open-source PTLens database.

The Lensfun database and this adapted data package are licensed under the
Creative Commons Attribution-ShareAlike 3.0 Unported license
(`CC-BY-SA-3.0`). No payment, subscription, or separate permission is required
to use the data while complying with that license.

## Changes made by Celvion Stack

This is an adaptation rather than an unmodified copy. The build-time converter:

1. transforms Lensfun database-version-2 XML into deterministic UTF-8 JSON;
2. retains mount, camera, lens, projection, crop-factor, aspect-ratio,
   lens-center, and geometric-distortion data;
3. retains the exact Lensfun model of every distortion sample: `ptlens`,
   `poly3`, or `poly5`;
4. preserves nominal focal length, resolved real focal length, whether real
   focal length was measured, calibration crop factor, calibration aspect
   ratio, and the model-specific source coefficients;
5. makes Lensfun defaults explicit, converts localized text into ordered
   language/value arrays, and adds stable derived identifiers and source
   ordinals;
6. adds an SHA-256 manifest of every source XML file; and
7. omits TCA, vignetting, crop-boundary, and field-of-view calibration data,
   because Celvion Stack v1 consumes geometric distortion only.

No PTLens or Poly3 record is fitted or approximated as Brown–Conrady. The data
package preserves Lensfun's Hugin/PanoTools coordinate convention and equations
so a consumer can execute each source model directly.

## Reproduce the package

Check out Lensfun at the exact commit above, then run from this repository
root:

```sh
python3 Tools/lensfun/convert_lensfun_distortion.py \
  --lensfun-root /path/to/lensfun \
  --output lensfun-distortion-v1.json
```

Verify that the checked-in package is byte-for-byte reproducible:

```sh
python3 Tools/lensfun/convert_lensfun_distortion.py \
  --lensfun-root /path/to/lensfun \
  --output lensfun-distortion-v1.json \
  --check
```

The converter rejects any commit other than the pinned revision and also
verifies a hard-coded combined SHA-256 manifest for the 56 source XML files.
It uses only Python's standard library and does not compile or link Lensfun.

## Redistribution

When copying or modifying this data package:

- keep appropriate credit to the Lensfun database contributors and Tom
  Niemann;
- link to the Lensfun project and exact source revision;
- include the complete `CC-BY-SA-3.0` license or its URI;
- state whether you changed the data; and
- distribute adaptations under `CC-BY-SA-3.0` or another license permitted by
  its Section 4(b).

Celvion Stack's app code, UI, and original calibration code are separate works
and are not relicensed by this data-package notice.
