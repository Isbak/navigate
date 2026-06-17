"""Maintenance operations for the catalog.

Houses destructive housekeeping that sits above the per-layer repositories - for
now, the ``clean-source`` purge that permanently removes all material tied to a
file or folder (semantic candidates, classification, artifact rows, links, and
the extraction cache) and re-consolidates the knowledge graph.
"""

from __future__ import annotations

from .service import PurgeStats, purge_path

__all__ = ["PurgeStats", "purge_path"]
