# Lensfun build-time conversion

`convert_lensfun_distortion.py` converts Lensfun database-version-2 XML into
Celvion Stack's deterministic JSON package without compiling or linking the
Lensfun C++ library.

The converter is pinned to
`5bfb8d8cb151a3a4068219cfc798f63d0641ff19`. It validates both the Git revision
(when converting a checkout) and the combined SHA-256 manifest of the 56 XML
inputs. A source archive can be used because the XML manifest check still
protects against a falsely labelled or modified input tree.

The output retains exact `ptlens`, `poly3`, and `poly5` records and the source
coordinate inputs. Do not add a conversion from PTLens to Brown–Conrady: the
two polynomials have different terms and focal-length-preserving scaling
semantics.

Generate and verify using the commands documented in the repository root
[`README.md`](../../README.md).

Validate the checked-in package from either the Celvion Stack source tree or
the standalone public data repository:

```sh
python3 Tools/lensfun/verify_lensfun_package.py
```
