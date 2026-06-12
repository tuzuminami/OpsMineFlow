# OpsMineFlow Agent Notes

## Product Guardrails

- Keep the project Apache-2.0.
- Do not add AGPL, GPL, LGPL, SSPL, Commons Clause, Business Source License, Polyform, or non-commercial dependencies.
- Do not add LLM, local LLM, cloud AI, telemetry, analytics, remote crash reporting, or update-check integrations.
- Runtime network access must remain local-only: localhost, 127.0.0.1, file, and Tauri internal channels.
- Do not implement keystroke logging, password capture, screenshot capture, screen recording, microphone access, or camera access.
- Treat ActivityWatch as optional import data only. Do not copy, vendor, or directly depend on ActivityWatch code.
- Process mining algorithms must be implemented locally without PM4Py.

## Engineering Workflow

- Prefer small commits that match feature boundaries.
- Keep unclear product questions in `docs/OPEN_QUESTIONS.md` and keep implementation moving with conservative assumptions.
- Run `./scripts/test.sh`, `./scripts/lint.sh`, `./scripts/check_licenses.sh`, and `./scripts/check_no_external_network.sh` before push.
- If GitHub push fails, record the error in `docs/operations/GITHUB_PUSH_ISSUES.md`.

