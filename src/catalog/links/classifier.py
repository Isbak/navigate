"""Deterministic classification of normalized URLs.

Three independent classifications are produced for every link:

* ``target_system`` - which knowledge system the URL points at
  (sharepoint, confluence, azure_devops, ...).
* ``target_type``   - what kind of thing it points at within that system
  (document, work_item, pull_request, ...).
* ``link_kind``     - a coarse internal / external / local / email bucket.

Everything is pattern based: no network calls, no LLM, no RDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import LinkConfig
from .normalizer import is_local_path, is_mailto

# Systems considered "internal" company knowledge systems for ``link_kind``.
INTERNAL_SYSTEMS = frozenset(
    {"sharepoint", "onedrive", "confluence", "jira", "azure_devops", "teams"}
)

TARGET_SYSTEMS = (
    "sharepoint",
    "onedrive",
    "confluence",
    "jira",
    "azure_devops",
    "github",
    "teams",
    "email",
    "local_file",
    "external_web",
    "unknown",
)


@dataclass(frozen=True)
class Classification:
    target_system: str
    target_type: str
    link_kind: str


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.netloc.lower(), parts.path.lower()


def classify_target_system(normalized_url: str, config: LinkConfig | None = None) -> str:
    """Map ``normalized_url`` to one of :data:`TARGET_SYSTEMS`."""

    config = config or LinkConfig.empty()
    if is_mailto(normalized_url):
        return "email"
    if is_local_path(normalized_url):
        return "local_file"

    host, path = _split(normalized_url)
    if not host:
        return "unknown"

    # OneDrive before SharePoint: personal OneDrive uses ``*-my.sharepoint.com``.
    if "onedrive.live.com" in host or "1drv.ms" in host or "my.sharepoint.com" in host:
        return "onedrive"
    if "sharepoint.com" in host or any(
        token in path for token in ("/sites/", "/:w:/", "/:x:/", "/:p:/")
    ):
        return "sharepoint"
    if (
        "dev.azure.com" in host
        or "visualstudio.com" in host
        or any(token in path for token in ("_workitems", "_git", "_wiki", "pullrequest"))
    ):
        return "azure_devops"
    if (host.startswith("jira.") or ".jira." in host or "atlassian.net" in host) and (
        "/browse/" in path or "/jira/" in path
    ):
        return "jira"
    if host.startswith("confluence.") or ".confluence." in host or (
        ("atlassian.net" in host or "confluence" in host)
        and ("/wiki/" in path or "/display/" in path)
    ):
        return "confluence"
    if "/wiki/" in path or "/display/" in path:
        return "confluence"
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if "teams.microsoft.com" in host or any(
        token in path for token in ("/l/message/", "/l/channel/", "/l/meetup-join/")
    ):
        return "teams"

    # User-defined system domains extend the built-in patterns.
    configured = config.system_for_host(host)
    if configured is not None:
        return configured

    return "external_web"


_DOC_EXTENSIONS = (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".md", ".txt")


def classify_target_type(normalized_url: str, target_system: str) -> str:
    """Infer ``target_type`` from the URL within its system, best effort."""

    host, path = _split(normalized_url)

    if target_system == "email":
        return "email_address"

    if target_system in ("sharepoint", "onedrive"):
        if any(token in path for token in ("/:w:/", "/:x:/", "/:p:/")):
            return "document"
        if path.endswith(_DOC_EXTENSIONS):
            return "document"
        if "/personal/" in path and (path.endswith("/") or "/documents" in path):
            return "folder"
        return "document"

    if target_system == "azure_devops":
        if "_workitems" in path:
            return "work_item"
        if "_git" in path or "pullrequest" in path:
            return "pull_request" if "pullrequest" in path else "repository"
        if "_wiki" in path:
            return "wiki_page"
        return "unknown"

    if target_system == "github":
        if "/pull/" in path:
            return "pull_request"
        # github.com/<owner>/<repo>[/...] looks like a repository.
        segments = [seg for seg in path.split("/") if seg]
        if len(segments) >= 2:
            return "repository"
        return "unknown"

    if target_system == "teams":
        if "/l/message/" in path:
            return "message"
        if "/l/channel/" in path:
            return "channel"
        if "/l/meetup-join/" in path:
            return "meeting"
        return "unknown"

    if target_system == "confluence":
        return "wiki_page"

    if target_system == "jira":
        return "work_item"

    if target_system == "local_file":
        if path.endswith(_DOC_EXTENSIONS):
            return "document"
        if normalized_url.endswith("/"):
            return "folder"
        return "unknown"

    if target_system == "external_web":
        if path.endswith(_DOC_EXTENSIONS):
            return "document"
        return "unknown"

    return "unknown"


def classify_link_kind(
    normalized_url: str, target_system: str, config: LinkConfig | None = None
) -> str:
    """Bucket a link into internal / external / local / email / unknown."""

    config = config or LinkConfig.empty()

    if target_system == "email":
        return "email"
    if target_system == "local_file":
        return "local"

    # An explicit user-configured internal domain always wins.
    if config.is_internal(normalized_url):
        return "internal"

    if target_system in INTERNAL_SYSTEMS:
        return "internal"
    if target_system in ("github", "external_web"):
        return "external"
    return "unknown"


def classify(
    normalized_url: str, config: LinkConfig | None = None
) -> Classification:
    """Run all three classifiers and return them together."""

    config = config or LinkConfig.empty()
    system = classify_target_system(normalized_url, config)
    target_type = classify_target_type(normalized_url, system)
    kind = classify_link_kind(normalized_url, system, config)
    return Classification(target_system=system, target_type=target_type, link_kind=kind)


__all__ = [
    "INTERNAL_SYSTEMS",
    "TARGET_SYSTEMS",
    "Classification",
    "classify",
    "classify_target_system",
    "classify_target_type",
    "classify_link_kind",
]
