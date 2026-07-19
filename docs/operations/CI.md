# CI / main branch quality gate

## Purpose

The `CI` workflow is the required macOS quality gate for every pull request and every push to `main`. It uses no repository secrets, defaults to read-only repository permissions, and does not publish artifacts, tags, releases, or telemetry.

The stable required check names are **`macos-core`** and **`macos-package-smoke`**. Keep those names stable when editing the workflow; GitHub main-branch protection references them exactly.

## What CI verifies

- Python 3.11 virtual-environment installation and Python test dependencies
- `npm ci` against `apps/desktop/package-lock.json`, which rejects Node dependency/lockfile drift
- `cargo check --locked` for the Tauri desktop shell
- Swift type checking through `./scripts/test.sh`
- `./scripts/test.sh`
- `./scripts/lint.sh`
- `./scripts/check_licenses.sh`
- `./scripts/check_no_external_network.sh`
- unsigned `.app`/`.dmg` package smoke plus `SHA256SUMS.txt` validation

The current workflow runs on GitHub's supported `macos-15` Apple Silicon runner. It proves that each artifact can be produced on that architecture; it does not establish an Intel support guarantee or a universal binary. #78 owns the v1 architecture policy and any Intel/universal release verification.

Python dependencies still use the project version constraints during this first CI slice. Hash-pinned Python dependency locking, SBOM, vulnerability scanning, and provenance are owned by #84; do not treat this workflow as satisfying those supply-chain controls.

## GitHub repository setting

After the workflow has run successfully on `main`, configure main-branch protection (or an equivalent repository ruleset) to:

1. Require a pull request before merging.
2. Require the `macos-core` and `macos-package-smoke` status checks and require them to be up to date.
3. Apply the rule to administrators as well as collaborators.
4. Do not allow force pushes or deletions of `main`.

The workflow itself deliberately does not hold permission to change protection. That configuration is a repository-admin action and is verified separately in issue #81.

## Fork and secret safety

The workflow runs on `pull_request`, not `pull_request_target`, and declares no secrets. Every third-party action is pinned to a full commit SHA. Keep both properties. A future notarization or release workflow must run only from a protected environment after explicit human approval; it must not share credentials with this workflow.
