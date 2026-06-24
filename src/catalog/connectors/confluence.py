"""Confluence connector: exports space pages as Markdown files.

Auth: Atlassian API token (email + api_token as HTTP Basic auth).
Pages are listed via the Confluence REST API v2 and their HTML body is
stripped to plain text and wrapped in a Markdown template that includes
metadata (space, page ID, version, last-updated). This makes the content
classifiable by the same LLM pipeline used for local documents.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime

import requests

from . import _http
from .base import BaseConnector, ConnectorAuthError, ConnectorError, RemoteDocument
from .config import ConnectorEntry

LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def _html_to_text(html: str) -> str:
    """Minimal HTML → plain-text conversion for Confluence storage format."""
    text = html.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = _TAG_RE.sub(" ", text)
    lines = (ln.strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln)


def _to_markdown(title: str, space_key: str, page_id: str, body: str, version: int, updated: str) -> str:
    content = _html_to_text(body)
    return (
        f"# {title}\n\n"
        f"**Space**: {space_key}  \n"
        f"**Page ID**: {page_id}  \n"
        f"**Version**: {version}  \n"
        f"**Last updated**: {updated}\n\n"
        "---\n\n"
        f"{content}\n"
    )


class ConfluenceConnector(BaseConnector):
    """Syncs pages from Confluence spaces as Markdown via REST API v2."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        creds = entry.credentials
        base_url = creds.get("url", "").rstrip("/")
        if not base_url:
            raise ConnectorError(
                f"Confluence connector {entry.name!r}: credentials.url is required"
            )
        self._api_base = f"{base_url}/wiki/api/v2"
        self._spaces: list[str] = entry.settings.get("spaces", [])

        email = creds.get("email", "")
        token = creds.get("api_token", "")
        self._session = requests.Session()
        if email and token:
            self._session.auth = (email, token)
        elif token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        else:
            raise ConnectorAuthError(
                f"Confluence connector {entry.name!r}: "
                "credentials.email and credentials.api_token are required"
            )

    @property
    def name(self) -> str:
        return self._name

    def _space_id(self, space_key: str) -> str | None:
        data = _http.get_json(
            self._session, f"{self._api_base}/spaces",
            label="Confluence spaces",
            params={"keys": space_key, "limit": "1"},
        )
        results = data.get("results", [])
        if not results:
            LOGGER.warning("Confluence space %r not found", space_key)
            return None
        return str(results[0]["id"])

    def _iter_pages(self, space_key: str) -> Iterator[dict]:
        space_id = self._space_id(space_key)
        if space_id is None:
            return
        cursor: str | None = None
        while True:
            params: dict = {"space-id": space_id, "limit": "50", "status": "current"}
            if cursor:
                params["cursor"] = cursor
            data = _http.get_json(
                self._session, f"{self._api_base}/pages",
                label=f"Confluence pages {space_key}", params=params,
            )
            yield from data.get("results", [])
            next_link: str = data.get("_links", {}).get("next", "")
            if not next_link:
                break
            qs = urllib.parse.urlparse(next_link).query
            cursor = urllib.parse.parse_qs(qs).get("cursor", [None])[0]
            if not cursor:
                break

    def list_documents(self) -> Iterator[RemoteDocument]:
        for space_key in self._spaces:
            try:
                for page in self._iter_pages(space_key):
                    page_id = str(page.get("id", ""))
                    title: str = page.get("title", page_id)
                    ver_info: dict = page.get("version", {}) or {}
                    version: int = ver_info.get("number", 1)
                    created: str = page.get("createdAt") or datetime.now(UTC).isoformat()
                    updated: str = ver_info.get("createdAt") or created
                    safe_title = _SAFE_NAME_RE.sub("_", title)[:100]
                    yield RemoteDocument(
                        remote_id=f"{space_key}/{page_id}",
                        name=f"{safe_title}.md",
                        file_type="md",
                        size_bytes=0,
                        created_at=created,
                        # Append version number so a page edit is detected as changed.
                        modified_at=f"{updated}:v{version}",
                    )
            except ConnectorError as exc:
                LOGGER.error("Failed to list Confluence space %s: %s", space_key, exc)

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        space_key, page_id = doc.remote_id.split("/", 1)
        data = _http.get_json(
            self._session, f"{self._api_base}/pages/{page_id}",
            label=f"Confluence page {page_id}",
            params={"body-format": "storage"},
        )
        title: str = data.get("title", page_id)
        body: str = data.get("body", {}).get("storage", {}).get("value", "")
        ver_info: dict = data.get("version", {}) or {}
        version: int = ver_info.get("number", 1)
        updated: str = ver_info.get("createdAt", "")
        return _to_markdown(title, space_key, page_id, body, version, updated).encode("utf-8")


def build(entry: ConnectorEntry) -> ConfluenceConnector:
    return ConfluenceConnector(entry)
