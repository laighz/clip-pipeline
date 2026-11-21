"""HTTPS reads with an explicit scheme check.

Callers pass full https URLs. ``file:`` and every other scheme are rejected
before anything is opened, which is the check Bandit B310 asks for.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit


class HttpsError(ValueError):
    pass


def https_url(host: str, path: str) -> str:
    """Build an https URL. Host and path stay separate at the call site."""
    return "https://" + host + path


def read_https(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    method: str | None = None,
    limit: int | None = None,
) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HttpsError(f"nur https ist erlaubt, nicht {parsed.scheme or 'leer'}")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    # Scheme is checked above. Bandit still flags every urlopen.
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return resp.read() if limit is None else resp.read(limit)
