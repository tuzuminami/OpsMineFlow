# License Policy

## Allowed Direct Dependency Licenses

- Apache-2.0
- MIT
- BSD-2-Clause
- BSD-3-Clause
- ISC
- Zlib
- Unlicense
- CC0
- PSF License

## Prohibited Direct Dependency Licenses

- AGPL
- GPL
- LGPL
- SSPL
- Commons Clause
- Business Source License
- Polyform Noncommercial
- Creative Commons NonCommercial
- CC BY-NC
- Custom commercial-restriction licenses
- Unknown licenses

## Review Steps

1. Identify the package, source repository, license, and transitive dependencies.
2. Confirm commercial use and redistribution are allowed.
3. Confirm no external telemetry or SaaS coupling is introduced.
4. Add the dependency to `docs/licenses/THIRD_PARTY_LICENSES.md`.
5. Run `./scripts/check_licenses.sh`.

