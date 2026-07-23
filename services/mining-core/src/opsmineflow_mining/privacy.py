from __future__ import annotations

import re
from urllib.parse import urlparse

SECRET_HINTS = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|認証|パスワード)",
    re.IGNORECASE,
)

# File imports do not retain a source person identifier.  A constant still
# lets the local analysis distinguish imported events from native recording
# without creating a per-person profile from a name, email address, or alias.
IMPORTED_USER_ALIAS = "imported-user"


def extract_domain(url: str) -> str:
    """Return only a hostname suitable for local persistence and export.

    ``netloc`` is intentionally not used: it may include credentials and a
    port (for example ``person:secret@example.test:8443``).  Import sources
    are not trusted to have supplied a fully-qualified URL, so the same
    hostname-only policy is applied to scheme-less legacy values as well.
    """

    if not url:
        return ""
    value = url.strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value else f"//{value}")
        return (parsed.hostname or "").casefold()
    except ValueError:
        # Invalid bracketed IPv6 and malformed ports must not leak their raw
        # source value through a fallback string.
        return ""


def mask_url(url: str, keep_domain_only: bool = True) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "[masked-url]"
    if keep_domain_only:
        return f"{parsed.scheme}://{parsed.netloc}/[masked]"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def mask_window_title(title: str) -> str:
    value = (title or "").strip()
    if not value:
        return ""
    if SECRET_HINTS.search(value):
        return "[confidential-title]"
    if len(value) <= 18:
        return value
    return f"{value[:8]}...[masked]"


def looks_confidential(*values: str) -> bool:
    return any(SECRET_HINTS.search(value or "") for value in values)
