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


def requirement_display_name(standard_name: str, clause_ref: str, title: str) -> str:
    """Pick a stable, human-readable name for a Requirement object.

    A requirement is best identified by its standard and clause locator
    (``GDPR Art. 32``); we fall back to the clause ref or title alone, and
    finally to ``Requirement`` so a name is always producible.

    >>> requirement_display_name("GDPR", "Art. 32", "Security of processing")
    'GDPR Art. 32'
    >>> requirement_display_name("", "", "Data minimisation")
    'Data minimisation'
    """

    standard = (standard_name or "").strip()
    clause = (clause_ref or "").strip()
    title = (title or "").strip()
    if standard and clause:
        return f"{standard} {clause}"
    if clause:
        return clause
    if title:
        return title
    return "Requirement"


def equation_display_name(standard_name: str, symbol: str, clause_ref: str) -> str:
    """Pick a stable, human-readable name for an Equation object.

    An equation is best identified by its standard and result symbol
    (``EN 1992-1-1 V_Rd_c``); we fall back to the symbol, then the standard and
    clause, then the clause alone, and finally ``Equation`` so a name is always
    producible.

    >>> equation_display_name("EN 1992-1-1", "V_Rd_c", "6.2.2(1)")
    'EN 1992-1-1 V_Rd_c'
    >>> equation_display_name("", "", "6.2.2(1)")
    '6.2.2(1)'
    """

    standard = (standard_name or "").strip()
    symbol = (symbol or "").strip()
    clause = (clause_ref or "").strip()
    if standard and symbol:
        return f"{standard} {symbol}"
    if symbol:
        return symbol
    if standard and clause:
        return f"{standard} {clause}"
    if clause:
        return clause
    return "Equation"


__all__ = [
    "slugify",
    "object_id",
    "requirement_display_name",
    "equation_display_name",
]
