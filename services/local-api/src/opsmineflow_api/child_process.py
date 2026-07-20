from __future__ import annotations

import os


_SAFE_SUBPROCESS_ENVIRONMENT_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR")


def sanitized_subprocess_environment() -> dict[str, str]:
    """Return the minimum ambient environment needed by local diagnostic tools."""

    return {
        key: value
        for key in _SAFE_SUBPROCESS_ENVIRONMENT_KEYS
        if (value := os.environ.get(key))
    }


def recording_agent_environment(token: str) -> dict[str, str]:
    """Pass only the recording credential to the native recorder child process."""

    return {"OPSMINEFLOW_RECORDING_TOKEN": token}
