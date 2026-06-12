from __future__ import annotations

import re
from urllib.parse import urlparse

SECRET_HINTS = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|認証|パスワード)",
    re.IGNORECASE,
)


def extract_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.netloc.lower()


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

