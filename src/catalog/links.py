from __future__ import annotations

import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)


def classify_target_system(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "sharepoint.com" in host:
        return "sharepoint"
    if "onedrive.live.com" in host or "1drv.ms" in host:
        return "onedrive"
    if "confluence" in host or "/wiki" in path:
        return "confluence/wiki"
    if "atlassian.net" in host and ("/jira" in path or "/browse/" in path):
        return "jira"
    if "dev.azure.com" in host or "visualstudio.com" in host:
        return "azure_devops"
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if "teams.microsoft.com" in host:
        return "teams"
    if parsed.scheme in {"http", "https"} and host:
        return "external"
    return "unknown"


def classify_target_type(url: str) -> str:
    lower = url.lower()
    if lower.endswith((".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt")):
        return "document"
    return "web"


def extract_links_from_text(text: str) -> list[dict[str, str | None]]:
    found: dict[str, str | None] = {}
    for anchor, url in MARKDOWN_LINK_RE.findall(text):
        found[url] = anchor
    for url in URL_RE.findall(text):
        url = url.rstrip(".,;:")
        found.setdefault(url, None)
    return [{"target_url": url, "anchor_text": anchor} for url, anchor in found.items()]
