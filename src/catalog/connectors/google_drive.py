"""Google Drive connector via Drive API v3.

Auth: service-account JSON key file only. The JWT is signed with the private
key using ``cryptography`` (already a core dependency), so no google-auth
package is required.

Google-native file types (Docs, Sheets, Slides) are exported to the nearest
Office format that the extraction pipeline already handles. Binary files
(PDF, DOCX, ...) are downloaded directly. Folders are traversed recursively.
"""

from __future__ import annotations

import json
import logging
import os
import time
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from datetime import UTC, datetime

import requests

from . import _http
from .base import BaseConnector, ConnectorAuthError, ConnectorError, RemoteDocument
from .config import ConnectorEntry

LOGGER = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/drive/v3"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Google-native MIME → (export MIME, output extension)
_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
}

# Direct-download MIME types and their file extensions
_DIRECT_MIME: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/markdown": "md",
}


def _b64url(data: bytes) -> bytes:
    return urlsafe_b64encode(data).rstrip(b"=")


def _sign_jwt(sa: dict) -> str:
    """Create a signed RS256 JWT for the service account using ``cryptography``."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise ConnectorError(
            "Google Drive connector requires the 'cryptography' package. "
            "Install it with: pip install cryptography"
        ) from exc

    private_key = serialization.load_pem_private_key(
        sa["private_key"].encode(), password=None
    )
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "iss": sa["client_email"],
        "scope": _SCOPE,
        "aud": sa["token_uri"],
        "exp": now + 3600,
        "iat": now,
    }).encode())
    signing_input = header + b"." + payload
    sig = _b64url(private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))
    return (signing_input + b"." + sig).decode()


def _fetch_access_token(sa: dict) -> tuple[str, float]:
    """Exchange a service-account JWT for an OAuth2 access token."""
    jwt = _sign_jwt(sa)
    resp = requests.post(
        sa["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        },
        timeout=30,
    )
    if resp.status_code in (400, 401):
        raise ConnectorAuthError(
            "Google Drive: service account auth failed. "
            "Check that the key file is valid and the service account has Drive access."
        )
    resp.raise_for_status()
    data = resp.json()
    expires_at = time.time() + data.get("expires_in", 3600) - 60
    return data["access_token"], expires_at


class GoogleDriveConnector(BaseConnector):
    """Syncs files from Google Drive folder trees via Drive API v3."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        sa_file = entry.credentials.get("service_account_file", "")
        if not sa_file:
            raise ConnectorError(
                f"Google Drive connector {entry.name!r}: "
                "credentials.service_account_file is required"
            )
        sa_path = os.path.expanduser(sa_file)
        with open(sa_path, encoding="utf-8") as fh:
            self._sa: dict = json.load(fh)

        self._folders: list[str] = [
            f["id"] if isinstance(f, dict) else str(f)
            for f in entry.settings.get("folders", [])
        ]
        raw_types: list | None = entry.settings.get("file_types")
        self._file_types: set[str] | None = set(raw_types) if raw_types else None

        self._token: str | None = None
        self._token_expires: float = 0.0
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return self._name

    def _ensure_token(self) -> None:
        if self._token is None or time.time() >= self._token_expires:
            self._token, self._token_expires = _fetch_access_token(self._sa)
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _is_supported(self, mime: str, name: str) -> bool:
        if self._file_types is not None:
            if mime in _EXPORT_MAP:
                _, ext = _EXPORT_MAP[mime]
            else:
                ext = _DIRECT_MIME.get(mime, "")
            return ext in self._file_types
        return mime in _DIRECT_MIME or mime in _EXPORT_MAP

    def _iter_folder(self, folder_id: str) -> Iterator[dict]:
        self._ensure_token()
        page_token: str | None = None
        while True:
            params: dict = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": (
                    "nextPageToken,"
                    "files(id,name,mimeType,size,createdTime,modifiedTime)"
                ),
                "pageSize": "100",
            }
            if page_token:
                params["pageToken"] = page_token
            data = _http.get_json(
                self._session, f"{_API_BASE}/files",
                label=f"Drive list {folder_id}", params=params,
            )
            for item in data.get("files", []):
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    yield from self._iter_folder(item["id"])
                else:
                    yield item
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    def list_documents(self) -> Iterator[RemoteDocument]:
        for folder_id in self._folders:
            try:
                for item in self._iter_folder(folder_id):
                    mime = item.get("mimeType", "")
                    name = item.get("name", item["id"])
                    if not self._is_supported(mime, name):
                        continue
                    if mime in _EXPORT_MAP:
                        _, ext = _EXPORT_MAP[mime]
                        if not name.lower().endswith("." + ext):
                            name = name + "." + ext
                    else:
                        ext = _DIRECT_MIME.get(mime, "")
                    now = datetime.now(UTC).isoformat()
                    yield RemoteDocument(
                        remote_id=item["id"],
                        name=name,
                        file_type=ext,
                        size_bytes=int(item.get("size", 0) or 0),
                        created_at=item.get("createdTime", now),
                        modified_at=item.get("modifiedTime", now),
                    )
            except ConnectorError as exc:
                LOGGER.error("Failed to list Drive folder %s: %s", folder_id, exc)

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        self._ensure_token()
        file_id = doc.remote_id
        # Determine MIME type to decide download vs. export.
        meta = _http.get_json(
            self._session, f"{_API_BASE}/files/{file_id}",
            label=f"Drive meta {file_id}",
            params={"fields": "mimeType"},
        )
        mime = meta.get("mimeType", "")
        if mime in _EXPORT_MAP:
            export_mime, _ = _EXPORT_MAP[mime]
            return _http.get_bytes(
                self._session,
                f"{_API_BASE}/files/{file_id}/export",
                label=f"Drive export {file_id}",
                params={"mimeType": export_mime},
            )
        return _http.get_bytes(
            self._session,
            f"{_API_BASE}/files/{file_id}",
            label=f"Drive download {file_id}",
            params={"alt": "media"},
        )


def build(entry: ConnectorEntry) -> GoogleDriveConnector:
    return GoogleDriveConnector(entry)
