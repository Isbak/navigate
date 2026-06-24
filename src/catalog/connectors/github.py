"""GitHub connector: syncs files from one or more repositories.

Uses the GitHub REST API v3 with a personal access token (PAT). The tree API
returns the full recursive file listing in one call, so list_documents() is
efficient even for large repos. Each file's blob SHA doubles as its version
indicator — a changed file gets a new SHA, triggering a re-download.

remote_id format: ``owner/repo@branch:path/to/file.ext``
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import requests

from ..scanner import CODE_EXTENSIONS, SUPPORTED_EXTENSIONS
from . import _http
from .base import BaseConnector, ConnectorError, RemoteDocument
from .config import ConnectorEntry

LOGGER = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_ALL_EXTENSIONS = SUPPORTED_EXTENSIONS | CODE_EXTENSIONS


def _ext(name: str) -> str:
    """Return lowercased extension without dot, or empty string for extensionless files."""
    idx = name.rfind(".")
    return name[idx + 1:].lower() if idx > 0 else ""


class GitHubConnector(BaseConnector):
    """Syncs files from GitHub repos via the REST API."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        token = entry.credentials.get("token", "")
        self._repos: list[str] = entry.settings.get("repos", [])
        self._branches: list[str] = entry.settings.get("branches", ["main"])
        raw_types: list | None = entry.settings.get("file_types")
        self._extensions: set[str] = (
            {f".{t.lstrip('.')}" for t in raw_types} if raw_types else _ALL_EXTENSIONS
        )

        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    @property
    def name(self) -> str:
        return self._name

    def _resolve_sha(self, owner: str, repo: str, branch: str) -> str | None:
        url = f"{_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch}"
        try:
            data = _http.get_json(self._session, url, label=f"GitHub ref {owner}/{repo}@{branch}")
            return data.get("object", {}).get("sha")
        except ConnectorError as exc:
            LOGGER.warning("Could not resolve ref %s/%s@%s: %s", owner, repo, branch, exc)
            return None

    def _list_tree(self, owner: str, repo: str, tree_sha: str) -> list[dict]:
        url = f"{_API_BASE}/repos/{owner}/{repo}/git/trees/{tree_sha}"
        data = _http.get_json(
            self._session, url,
            label=f"GitHub tree {owner}/{repo}",
            params={"recursive": "1"},
        )
        if data.get("truncated"):
            LOGGER.warning(
                "GitHub tree for %s/%s is truncated; very large repos may be incomplete",
                owner, repo,
            )
        return [item for item in data.get("tree", []) if item.get("type") == "blob"]

    def list_documents(self) -> Iterator[RemoteDocument]:
        for repo_full in self._repos:
            parts = repo_full.split("/", 1)
            if len(parts) != 2:
                LOGGER.warning("Invalid repo spec %r — expected owner/repo", repo_full)
                continue
            owner, repo = parts
            for branch in self._branches:
                sha = self._resolve_sha(owner, repo, branch)
                if sha is None:
                    continue
                try:
                    items = self._list_tree(owner, repo, sha)
                except ConnectorError as exc:
                    LOGGER.error("Failed to list tree for %s/%s: %s", owner, repo, exc)
                    continue
                for item in items:
                    path = item.get("path", "")
                    name = path.rsplit("/", 1)[-1]
                    if ("." + _ext(name)) not in self._extensions:
                        continue
                    blob_sha = item.get("sha", "")
                    yield RemoteDocument(
                        remote_id=f"{owner}/{repo}@{branch}:{path}",
                        name=name,
                        file_type=_ext(name),
                        size_bytes=item.get("size", 0),
                        created_at=datetime.now(UTC).isoformat(),
                        modified_at=blob_sha,  # blob SHA as version indicator
                    )

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        # remote_id: owner/repo@branch:path/to/file
        try:
            repo_branch, file_path = doc.remote_id.split(":", 1)
            owner_repo, branch = repo_branch.split("@", 1)
            owner, repo = owner_repo.split("/", 1)
        except ValueError as exc:
            raise ConnectorError(f"Malformed GitHub remote_id: {doc.remote_id!r}") from exc

        url = f"{_API_BASE}/repos/{owner}/{repo}/contents/{file_path}"
        # Include branch ref so we fetch the right version.
        data = _http.get_json(
            self._session, url,
            label=f"GitHub contents {doc.remote_id}",
            params={"ref": branch},
        )
        raw = data.get("content", "")
        return base64.b64decode(raw.replace("\n", ""))


def build(entry: ConnectorEntry) -> GitHubConnector:
    if not entry.credentials.get("token"):
        LOGGER.warning(
            "GitHub connector %r: no token set. "
            "Set credentials.token or GITHUB_TOKEN env var. "
            "Unauthenticated requests are rate-limited to 60/hour.",
            entry.name,
        )
    return GitHubConnector(entry)
