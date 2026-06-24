"""Abstract base types for remote content connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


class ConnectorError(RuntimeError):
    """Unrecoverable connector failure (bad credentials, endpoint down, etc.)."""


class ConnectorAuthError(ConnectorError):
    """Authentication or authorisation failure."""


@dataclass(frozen=True)
class RemoteDocument:
    """Metadata for a single item discovered in a remote source.

    ``modified_at`` is used as the version indicator for change detection; for
    Git-based sources (GitHub) it holds the blob SHA rather than a timestamp.
    """

    remote_id: str      # stable unique ID from the remote system
    name: str           # filename with extension (used for the local cache path)
    file_type: str      # extension without dot, e.g. "pdf", "md"
    size_bytes: int
    created_at: str     # ISO-8601 UTC
    modified_at: str    # ISO-8601 UTC *or* a content SHA for Git-based sources


@dataclass
class ConnectorStats:
    """Aggregated counters for one connector sync run."""

    connector_name: str
    new_files: int = 0
    changed_files: int = 0
    unchanged_files: int = 0
    deleted_files: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "connector_name": self.connector_name,
            "new_files": self.new_files,
            "changed_files": self.changed_files,
            "unchanged_files": self.unchanged_files,
            "deleted_files": self.deleted_files,
            "errors": self.errors,
        }


class BaseConnector(ABC):
    """Interface every connector must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """User-defined connector name (from config); becomes ``source_system`` on artifacts."""
        ...

    @abstractmethod
    def list_documents(self) -> Iterator[RemoteDocument]:
        """Yield metadata for every document available in this source."""
        ...

    @abstractmethod
    def fetch_content(self, doc: RemoteDocument) -> bytes:
        """Download and return the full binary content of a remote document."""
        ...
