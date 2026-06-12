# Troubleshooting

## Python Version

Use Python 3.11 or newer for development. If the system `python3` is older, install a newer Python and create a virtual environment manually.

## API Does Not Start

Confirm the API binds only to `127.0.0.1`. Do not bind to `0.0.0.0`.

## ActivityWatch Not Found

ActivityWatch is optional. Import exported JSON or CSV if the local ActivityWatch API is not running.

## draw.io File Does Not Open

Validate the export as XML and confirm it contains `mxfile`, `diagram`, `mxGraphModel`, and `root`.

