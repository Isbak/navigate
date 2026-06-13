"""URL normalization for the link discovery layer.

Normalization produces a stable ``normalized_url`` used for deduplication and
classification while the caller keeps the original ``raw_url`` untouched. The
rules here are deterministic and contain no network access or LLM use.
"""

from __future__ import annotations

import re
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urlsplit,
    urlunsplit,
)

# Tracking parameters stripped from every URL's query string.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
    }
)

# Trailing characters that are almost never part of a real URL when a link is
# lifted out of prose (e.g. ``see https://x.com.`` or ``(https://x.com)``).
_TRAILING_PUNCTUATION = ".,;:!?”’\"')]}>"

# A bare local absolute path, e.g. ``/home/user/file.txt`` or ``C:\\docs\\f``.
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _strip_trailing_punctuation(url: str) -> str:
    return url.rstrip(_TRAILING_PUNCTUATION)


def is_mailto(url: str) -> bool:
    return url.strip().lower().startswith("mailto:")


def is_local_path(url: str) -> bool:
    """True for ``file://`` URLs and bare local absolute paths."""

    stripped = url.strip()
    if stripped.lower().startswith("file:"):
        return True
    if stripped.startswith("/") or stripped.startswith("\\\\"):
        return True
    return bool(_WINDOWS_PATH_RE.match(stripped))


def normalize_mailto(url: str) -> str:
    """Reduce a ``mailto:`` link to ``mailto:<lowercased-address>``.

    Any ``?subject=`` style headers are dropped; the address identifies the
    target. Multiple comma-separated recipients are preserved in order.
    """

    body = url.strip()[len("mailto:") :]
    body = body.split("?", 1)[0]
    addresses = [unquote(part).strip().lower() for part in body.split(",") if part.strip()]
    return "mailto:" + ",".join(addresses)


def normalize_file_url(url: str) -> str:
    """Normalize a ``file://`` URL or bare local path to a ``file://`` form."""

    stripped = url.strip().replace("\\", "/")
    if stripped.lower().startswith("file:"):
        parts = urlsplit(stripped)
        host = parts.netloc.lower()
        path = parts.path
        return urlunsplit(("file", host, path, "", ""))
    # Bare local absolute path -> file:///path
    if _WINDOWS_PATH_RE.match(url.strip()):
        # Windows drive path: file:///C:/...
        return "file:///" + stripped.lstrip("/")
    return "file://" + stripped


def _fragment_is_meaningful(fragment: str) -> bool:
    """Keep fragments that look like routing/state, drop plain anchors.

    Single-page apps (and some wikis/ADO links) carry state in the fragment, so
    a fragment is preserved when it is route-like (contains ``/``), a hashbang
    (``!...``), or carries parameters (``=``). A plain ``#section`` anchor is
    dropped because it does not change the target document's identity.
    """

    if not fragment:
        return False
    return fragment.startswith("!") or "/" in fragment or "=" in fragment


def _normalize_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlencode(kept)


def normalize_url(raw_url: str) -> str:
    """Return a normalized, deduplication-friendly form of ``raw_url``.

    Steps: trim whitespace, drop trailing punctuation, lowercase the scheme and
    host, strip tracking parameters, drop non-meaningful fragments, and handle
    ``mailto:`` and ``file://``/local paths specially. The raw URL is never
    modified; callers persist it alongside the result.
    """

    if raw_url is None:
        return ""
    url = raw_url.strip()
    if not url:
        return ""

    if is_mailto(url):
        return normalize_mailto(_strip_trailing_punctuation(url))

    if is_local_path(url):
        return normalize_file_url(_strip_trailing_punctuation(url))

    url = _strip_trailing_punctuation(url)
    parts = urlsplit(url)

    # Without a scheme there is nothing reliable to normalize; return trimmed.
    if not parts.scheme:
        return url

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    query = _normalize_query(parts.query)
    fragment = parts.fragment if _fragment_is_meaningful(parts.fragment) else ""

    return urlunsplit((scheme, netloc, path, query, fragment))


__all__ = [
    "TRACKING_PARAMS",
    "is_mailto",
    "is_local_path",
    "normalize_mailto",
    "normalize_file_url",
    "normalize_url",
]
