"""Link discovery and relationship mapping.

This package reads the raw hyperlinks captured during extraction
(``cache/<artifact_id>/links.json``), normalizes and classifies them with
deterministic rules, and persists the results in SQLite. It is intentionally
separate from extraction: extraction writes cache files, discovery reads them.

Future phases (link resolution, broken-link checking, metadata fetching from
SharePoint/Confluence/ADO, LLM relationship classification, RDF export, graph
loading) can build on the normalized links and classifications produced here
without changing extraction or the scanner.
"""

from __future__ import annotations

from .classifier import (
    Classification,
    classify,
    classify_link_kind,
    classify_target_system,
    classify_target_type,
)
from .config import LinkConfig, load_link_config
from .normalizer import normalize_url
from .service import LinkScanStats, discover_links

__all__ = [
    "Classification",
    "LinkConfig",
    "LinkScanStats",
    "classify",
    "classify_link_kind",
    "classify_target_system",
    "classify_target_type",
    "discover_links",
    "load_link_config",
    "normalize_url",
]
