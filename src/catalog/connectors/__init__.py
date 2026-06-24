"""Remote content connectors: Google Drive, GitHub, SharePoint, Confluence, Jira."""

from __future__ import annotations

from .base import BaseConnector, ConnectorError, ConnectorStats, RemoteDocument
from .config import ConnectorEntry, ConnectorsConfig, load_connectors_config


def build_connector(entry: ConnectorEntry) -> BaseConnector:
    """Instantiate the correct connector for the given config entry."""

    if entry.type == "github":
        from .github import build as _b_github
        return _b_github(entry)
    if entry.type == "google_drive":
        from .google_drive import build as _b_gdrive
        return _b_gdrive(entry)
    if entry.type == "sharepoint":
        from .sharepoint import build as _b_sp
        return _b_sp(entry)
    if entry.type == "confluence":
        from .confluence import build as _b_conf
        return _b_conf(entry)
    if entry.type in ("jira", "azure_devops"):
        from .jira import build as _b_jira
        return _b_jira(entry)
    raise ConnectorError(f"Unknown connector type: {entry.type!r}. "
                         "Supported: github, google_drive, sharepoint, confluence, jira, azure_devops")


__all__ = [
    "BaseConnector",
    "ConnectorError",
    "ConnectorStats",
    "RemoteDocument",
    "ConnectorEntry",
    "ConnectorsConfig",
    "load_connectors_config",
    "build_connector",
]
