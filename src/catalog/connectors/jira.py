"""Jira and Azure DevOps connectors: export issues/work-items as Markdown files.

Both issue trackers use the same ``type`` dispatch pattern:
  - ``type: jira``         → JiraConnector (Atlassian Cloud/Server REST API v3)
  - ``type: azure_devops`` → AzureDevOpsConnector (Azure DevOps REST API 7.0)

Each issue or work item is rendered as a self-contained Markdown document
with metadata in a front-matter-style header so the LLM classification
pipeline can extract entities, decisions, and risks from it.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import requests

from . import _http
from .base import BaseConnector, ConnectorAuthError, ConnectorError, RemoteDocument
from .config import ConnectorEntry

LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def _strip_html(text: object) -> str:
    return _TAG_RE.sub(" ", str(text or ""))


def _safe_filename(s: str, max_len: int = 100) -> str:
    return _SAFE_NAME_RE.sub("_", s)[:max_len]


# ──────────────────────────────────── Jira ────────────────────────────────────

def _adf_to_text(node: object) -> str:
    """Extract plain text from an Atlassian Document Format (ADF) node."""
    if not isinstance(node, dict):
        return str(node) if node else ""
    parts = [str(node.get("text", ""))]
    for child in node.get("content", []):
        parts.append(_adf_to_text(child))
    return " ".join(p for p in parts if p)


def _render_jira_issue(issue: dict) -> str:
    fields: dict = issue.get("fields", {}) or {}
    key: str = issue.get("key", "")
    summary: str = fields.get("summary", "")
    itype: str = (fields.get("issuetype") or {}).get("name", "")
    status: str = (fields.get("status") or {}).get("name", "")
    priority: str = (fields.get("priority") or {}).get("name", "")
    assignee: str = (fields.get("assignee") or {}).get("displayName", "Unassigned")
    reporter: str = (fields.get("reporter") or {}).get("displayName", "")
    created: str = fields.get("created", "")
    updated: str = fields.get("updated", "")
    labels: str = ", ".join(fields.get("labels") or [])

    desc = fields.get("description") or ""
    description: str = (
        _adf_to_text(desc) if isinstance(desc, dict) else _strip_html(desc)
    )

    lines = [
        f"# [{key}] {summary}", "",
        f"**Type**: {itype}  ",
        f"**Status**: {status}  ",
        f"**Priority**: {priority}  ",
        f"**Assignee**: {assignee}  ",
        f"**Reporter**: {reporter}  ",
        f"**Created**: {created}  ",
        f"**Updated**: {updated}  ",
    ]
    if labels:
        lines.append(f"**Labels**: {labels}  ")
    lines += ["", "## Description", "", description or "_No description_", ""]

    comments: list = (fields.get("comment") or {}).get("comments", [])
    if comments:
        lines += ["## Comments", ""]
        for c in comments:
            author: str = (c.get("author") or {}).get("displayName", "")
            ts: str = c.get("created", "")
            body = c.get("body") or ""
            body_text = _adf_to_text(body) if isinstance(body, dict) else _strip_html(body)
            lines += [f"**{author}** ({ts}):", f"> {body_text}", ""]

    return "\n".join(lines)


class JiraConnector(BaseConnector):
    """Exports Jira issues from configured projects as Markdown files."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        creds = entry.credentials
        base_url = creds.get("url", "").rstrip("/")
        if not base_url:
            raise ConnectorError(
                f"Jira connector {entry.name!r}: credentials.url is required"
            )
        self._api_base = f"{base_url}/rest/api/3"
        self._projects: list[str] = entry.settings.get("projects", [])
        self._issue_types: list[str] = entry.settings.get("issue_types", [])

        email = creds.get("email", "")
        token = creds.get("api_token", "")
        self._session = requests.Session()
        if email and token:
            self._session.auth = (email, token)
        elif token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        else:
            raise ConnectorAuthError(
                f"Jira connector {entry.name!r}: "
                "credentials.email and credentials.api_token are required"
            )

    @property
    def name(self) -> str:
        return self._name

    def _jql(self) -> str:
        parts: list[str] = []
        if self._projects:
            proj = ", ".join(f'"{p}"' for p in self._projects)
            parts.append(f"project IN ({proj})")
        if self._issue_types:
            types = ", ".join(f'"{t}"' for t in self._issue_types)
            parts.append(f"issuetype IN ({types})")
        base = " AND ".join(parts) if parts else "ORDER BY updated DESC"
        return base + " ORDER BY updated DESC" if parts else base

    def list_documents(self) -> Iterator[RemoteDocument]:
        jql = self._jql()
        start = 0
        page_size = 50
        _list_fields = "summary,issuetype,status,priority,assignee,reporter,created,updated,labels"
        while True:
            data = _http.get_json(
                self._session, f"{self._api_base}/search",
                label="Jira search",
                params={"jql": jql, "startAt": start, "maxResults": page_size,
                        "fields": _list_fields},
            )
            issues: list = data.get("issues", [])
            if not issues:
                break
            for issue in issues:
                key: str = issue.get("key", "")
                fields: dict = issue.get("fields", {}) or {}
                updated: str = fields.get("updated") or datetime.now(UTC).isoformat()
                created: str = fields.get("created") or updated
                summary: str = fields.get("summary", key)
                yield RemoteDocument(
                    remote_id=key,
                    name=f"{_safe_filename(key + ' ' + summary)}.md",
                    file_type="md",
                    size_bytes=0,
                    created_at=created,
                    modified_at=updated,
                )
            start += len(issues)
            if start >= data.get("total", 0):
                break

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        data = _http.get_json(
            self._session, f"{self._api_base}/issue/{doc.remote_id}",
            label=f"Jira issue {doc.remote_id}",
            params={"fields": (
                "summary,issuetype,status,priority,assignee,reporter,"
                "created,updated,labels,description,comment"
            )},
        )
        return _render_jira_issue(data).encode("utf-8")


# ─────────────────────────────── Azure DevOps ─────────────────────────────────

def _render_ado_item(item: dict) -> str:
    fields: dict = item.get("fields", {}) or {}
    item_id = item.get("id", "")
    title: str = fields.get("System.Title", "")
    wi_type: str = fields.get("System.WorkItemType", "")
    state: str = fields.get("System.State", "")
    priority = fields.get("Microsoft.VSTS.Common.Priority", "")
    assigned_to: str = (fields.get("System.AssignedTo") or {}).get("displayName", "Unassigned")
    created_by: str = (fields.get("System.CreatedBy") or {}).get("displayName", "")
    created: str = fields.get("System.CreatedDate", "")
    changed: str = fields.get("System.ChangedDate", "")
    desc: str = _strip_html(fields.get("System.Description") or "")
    tags: str = fields.get("System.Tags", "") or ""

    lines = [
        f"# [{item_id}] {title}", "",
        f"**Type**: {wi_type}  ",
        f"**State**: {state}  ",
        f"**Priority**: {priority}  ",
        f"**Assigned To**: {assigned_to}  ",
        f"**Created By**: {created_by}  ",
        f"**Created**: {created}  ",
        f"**Updated**: {changed}  ",
    ]
    if tags:
        lines.append(f"**Tags**: {tags}  ")
    lines += ["", "## Description", "", desc or "_No description_", ""]
    return "\n".join(lines)


class AzureDevOpsConnector(BaseConnector):
    """Exports Azure DevOps work items from configured projects as Markdown files."""

    def __init__(self, entry: ConnectorEntry) -> None:
        self._name = entry.name
        creds = entry.credentials
        self._org = creds.get("organization", "").strip()
        if not self._org:
            raise ConnectorError(
                f"Azure DevOps connector {entry.name!r}: "
                "credentials.organization is required"
            )
        self._api_base = f"https://dev.azure.com/{self._org}"
        self._projects: list[str] = entry.settings.get("projects", [])
        self._wi_types: list[str] = entry.settings.get("work_item_types", [])

        token = creds.get("token", "")
        if not token:
            raise ConnectorAuthError(
                f"Azure DevOps connector {entry.name!r}: credentials.token is required"
            )
        b64 = base64.b64encode(f":{token}".encode()).decode()
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Basic {b64}"

    @property
    def name(self) -> str:
        return self._name

    def _wiql_ids(self, project: str) -> list[int]:
        type_clause = ""
        if self._wi_types:
            types = ", ".join(f"'{t}'" for t in self._wi_types)
            type_clause = f" AND [System.WorkItemType] IN ({types})"
        query = (
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{project}'{type_clause} "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        data = _http.post_json(
            self._session,
            f"{self._api_base}/{project}/_apis/wit/wiql",
            label=f"ADO WIQL {project}",
            params={"api-version": "7.0"},
            json={"query": query},
        )
        return [wi["id"] for wi in data.get("workItems", [])]

    def list_documents(self) -> Iterator[RemoteDocument]:
        for project in self._projects:
            try:
                ids = self._wiql_ids(project)
                for i in range(0, len(ids), 200):
                    batch = ids[i:i + 200]
                    data = _http.get_json(
                        self._session,
                        f"{self._api_base}/{project}/_apis/wit/workitems",
                        label=f"ADO work items {project}",
                        params={
                            "ids": ",".join(str(x) for x in batch),
                            "fields": (
                                "System.Id,System.Title,System.WorkItemType,"
                                "System.State,System.CreatedDate,System.ChangedDate"
                            ),
                            "api-version": "7.0",
                        },
                    )
                    for wi in data.get("value", []):
                        fields: dict = wi.get("fields", {}) or {}
                        wi_id = wi.get("id", "")
                        title: str = fields.get("System.Title", str(wi_id))
                        changed: str = fields.get("System.ChangedDate") or datetime.now(UTC).isoformat()
                        created: str = fields.get("System.CreatedDate") or changed
                        safe_name = _safe_filename(f"{project}-{wi_id} {title}")
                        yield RemoteDocument(
                            remote_id=f"{project}/{wi_id}",
                            name=f"{safe_name}.md",
                            file_type="md",
                            size_bytes=0,
                            created_at=created,
                            modified_at=changed,
                        )
            except ConnectorError as exc:
                LOGGER.error("Failed to list ADO project %s: %s", project, exc)

    def fetch_content(self, doc: RemoteDocument) -> bytes:
        project, wi_id = doc.remote_id.split("/", 1)
        data = _http.get_json(
            self._session,
            f"{self._api_base}/{project}/_apis/wit/workitems/{wi_id}",
            label=f"ADO work item {doc.remote_id}",
            params={"$expand": "all", "api-version": "7.0"},
        )
        return _render_ado_item(data).encode("utf-8")


def build(entry: ConnectorEntry) -> BaseConnector:
    if entry.type == "azure_devops":
        return AzureDevOpsConnector(entry)
    return JiraConnector(entry)
