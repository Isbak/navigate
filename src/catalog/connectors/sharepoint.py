"""SharePoint / OneDrive connector via Microsoft Graph API.

Auth: client-credentials OAuth2 flow (tenant_id + client_id + client_secret).
The access token is cached and refreshed automatically. Files are enumerated
recursively via the Graph drive-item children API and downloaded via the
pre-signed ``@microsoft.graph.downloadUrl`` field — no Graph auth header needed
for the actual download.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests

from . import _http
from .base import BaseConnector, ConnectorAuthError, ConnectorError, RemoteDocument
from .config import ConnectorEntry

LOGGER = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

_SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md"})


def _ext(name: str) -> str:
    idx = name.rfind(".")
    return name[idx + 1:].lower() if idx > 0 else ""


class SharePointConnector(BaseConnector):
    """Syncs documents from SharePoint sites and OneDrive drives via Graph API."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        creds = entry.credentials
        self._tenant_id = creds.get("tenant_id", "")
        self._client_id = creds.get("client_id", "")
        self._client_secret = creds.get("client_secret", "")
        self._sites: list[str] = [
            s["url"] if isinstance(s, dict) else str(s)
            for s in entry.settings.get("sites", [])
        ]
        raw_types: list | None = entry.settings.get("file_types")
        self._extensions: frozenset[str] = (
            frozenset(f".{t.lstrip('.')}" for t in raw_types)
            if raw_types else _SUPPORTED_EXTENSIONS
        )
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return self._name

    def _ensure_token(self) -> None:
        if self._token is not None and time.time() < self._token_expires:
            return
        url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=30,
        )
        if resp.status_code in (400, 401):
            raise ConnectorAuthError(
                f"SharePoint connector {self._name!r}: OAuth2 authentication failed. "
                "Check tenant_id, client_id, and client_secret."
            )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600) - 60
        self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _site_id(self, site_url: str) -> str:
        parsed = urlparse(site_url)
        hostname = parsed.netloc
        path = parsed.path.rstrip("/")
        data = _http.get_json(
            self._session, f"{_GRAPH_BASE}/sites/{hostname}:{path}",
            label=f"Graph site {site_url}",
        )
        site_id: str = data.get("id", "")
        if not site_id:
            raise ConnectorError(f"Could not resolve site ID for {site_url!r}")
        return site_id

    def _iter_items(self, site_id: str, url: str) -> Iterator[dict]:
        data = _http.get_json(self._session, url, label="Graph drive items")
        for item in data.get("value", []):
            if "folder" in item:
                children = (
                    f"{_GRAPH_BASE}/sites/{site_id}/drive/items/{item['id']}/children"
                )
                yield from self._iter_items(site_id, children)
            elif "file" in item:
                yield item
        next_link: str = data.get("@odata.nextLink", "")
        if next_link:
            yield from self._iter_items(site_id, next_link)

    def list_documents(self) -> Iterator[RemoteDocument]:
        self._ensure_token()
        for site_url in self._sites:
            try:
                site_id = self._site_id(site_url)
                root_url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root/children"
                for item in self._iter_items(site_id, root_url):
                    name: str = item.get("name", "")
                    if ("." + _ext(name)) not in self._extensions:
                        continue
                    modified = (
                        item.get("lastModifiedDateTime")
                        or item.get("createdDateTime")
                        or datetime.now(UTC).isoformat()
                    )
                    created = item.get("createdDateTime") or modified
                    yield RemoteDocument(
                        remote_id=item["id"],
                        name=name,
                        file_type=_ext(name),
                        size_bytes=item.get("size", 0),
                        created_at=created,
                        modified_at=modified,
                    )
            except ConnectorError as exc:
                LOGGER.error("Failed to sync SharePoint site %s: %s", site_url, exc)

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        self._ensure_token()
        # Try each configured site until the item is found.
        last_exc: Exception | None = None
        for site_url in self._sites:
            try:
                site_id = self._site_id(site_url)
                meta = _http.get_json(
                    self._session,
                    f"{_GRAPH_BASE}/sites/{site_id}/drive/items/{doc.remote_id}"
                    "?$select=@microsoft.graph.downloadUrl",
                    label=f"Graph download URL {doc.remote_id}",
                )
                dl_url: str = meta.get("@microsoft.graph.downloadUrl", "")
                if dl_url:
                    resp = requests.get(dl_url, timeout=120)
                    resp.raise_for_status()
                    return resp.content
            except ConnectorError as exc:
                last_exc = exc
                continue
        raise ConnectorError(
            f"Could not download {doc.remote_id!r} from any configured site"
        ) from last_exc


def build(entry: ConnectorEntry) -> SharePointConnector:
    for key in ("tenant_id", "client_id", "client_secret"):
        if not entry.credentials.get(key):
            raise ConnectorError(
                f"SharePoint connector {entry.name!r}: credentials.{key} is required"
            )
    return SharePointConnector(entry)
