"""Stable, URI-ready identifiers for knowledge objects.

Every knowledge object gets a deterministic id derived from its type and
canonical name, e.g. ``capability_release_governance`` or ``platform_salesforce``.
These ids are intentionally slug-like and stable across consolidation runs: the
same canonical object always resolves to the same id, which is what lets a
re-``consolidate`` preserve human review decisions, and what will later allow
each object to become an RDF resource without renaming anything.

Nothing here knows about SQL or RDF; it is pure string normalization.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase ``value`` and collapse non-alphanumerics into single ``_``.

    Returns ``"unnamed"`` for input that has no usable characters so an id is
    always producible.
    """

    slug = _SLUG_RE.sub("_", (value or "").strip().lower()).strip("_")
    return slug or "unnamed"


def object_id(object_type: str, canonical_name: str) -> str:
    """Return a stable id of the form ``<type>_<slug-of-name>``.

    >>> object_id("Capability", "Release Governance")
    'capability_release_governance'
    >>> object_id("Platform", "Salesforce")
    'platform_salesforce'
    """

    return f"{slugify(object_type)}_{slugify(canonical_name)}"


__all__ = ["slugify", "object_id"]
