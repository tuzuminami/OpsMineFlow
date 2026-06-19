# Runbook

## User Startup

```bash
./scripts/run_local.sh
```

The command starts the local API and WebUI on localhost.

## Local Development

```bash
./scripts/dev.sh
```

## Test

```bash
./scripts/test.sh
```

`./scripts/test.sh` includes unit tests, desktop type checks when dependencies are installed, and local API smoke checks.

## Policy Checks

```bash
./scripts/check_licenses.sh
./scripts/check_no_external_network.sh
```

## Export Review

Before sending output to a client, review masked fields, confidential flags, and the export preview.

The WebUI shows a warning before downloading an export. Treat that warning as the final manual checkpoint before client sharing.

Exports can be downloaded by the browser or saved by the local API to a user-provided local path.

## Local Data

OpsMineFlow stores runtime data in a local SQLite database by default. Set `OPSMINEFLOW_DATA_DIR` when a separate local workspace is required.
