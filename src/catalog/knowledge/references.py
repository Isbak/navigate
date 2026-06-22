"""Deterministic detection of cross-references in clause and standard text.

Standards rarely state their internal structure as tidy ``subject predicate
object`` triples; they cross-reference each other in prose - "in accordance with
clause 6.2", "see EN 1990", "as defined in 7.3.1". The semantic layer's
free-text relationship mining misses most of these, which is one reason
otherwise-related objects end up *floating* with no edge.

This module is the parsing half of the cross-reference assessment: pure,
offline, regex-based text scanning with no database or object knowledge. The
service layer resolves whatever these functions surface against the consolidated
objects, so a reference only becomes an edge when its target actually exists -
which keeps precision high regardless of how loosely we match here.
"""

from __future__ import annotations

import re

# A clause locator: a dotted decimal number, optionally with a trailing
# parenthetical sub-item ("6.2.2(1)"). Captured without the parenthetical so it
# lines up with how ``requirement_display_name`` keys a Requirement object.
_CLAUSE_NUMBER = r"(\d+(?:\.\d+)*)"

# "clause 6.2", "sub-clause 6.2.2", "section 7", "§6.2", "art. 5", "para 3".
_CLAUSE_KEYWORD_RE = re.compile(
    r"(?:clauses?|sub-?clauses?|sections?|paragraphs?|paras?|articles?"
    r"|art\.|cl\.|sec\.|§)\s*" + _CLAUSE_NUMBER,
    re.IGNORECASE,
)

# "see 6.2", "in accordance with 7.3.1", "as defined in 5.4" - a reference verb
# followed by a *dotted* number (at least two parts), so a bare "see 5" or an
# ordinary value like "factor 5" is not mistaken for a clause reference.
_CLAUSE_PHRASE_RE = re.compile(
    r"(?:see|refer(?:\s+to)?|in\s+accordance\s+with|according\s+to|as\s+per"
    r"|given\s+in|specified\s+in|defined\s+in|provided\s+in|described\s+in"
    r"|set\s+out\s+in|laid\s+down\s+in)\s+(\d+(?:\.\d+)+)",
    re.IGNORECASE,
)

# Continuation numbers in a list: "clauses 6.2, 6.3 and 6.4" - the keyword regex
# only catches the first, so pick up the dotted siblings joined by commas/"and".
_CLAUSE_LIST_RE = re.compile(
    r"(?:,|\band\b|&)\s*(\d+(?:\.\d+)+)",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")


def find_clause_references(text: str) -> list[str]:
    """Return clause locators referenced in ``text``, de-duplicated, in order.

    Detects keyword forms ("clause 6.2"), reference-verb forms ("in accordance
    with 7.3.1"), and the dotted siblings in a list ("clauses 6.2, 6.3 and
    6.4"). The list continuation is only harvested when a keyword/phrase match
    has already established that this run of numbers is a reference list, so a
    stray "1.5 and 2.0" in body text is not picked up.

    >>> find_clause_references("Design in accordance with clause 6.2 and 6.3.")
    ['6.2', '6.3']
    >>> find_clause_references("See 7.3.1 for the partial factors.")
    ['7.3.1']
    """

    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    anchored = False
    for match in _CLAUSE_KEYWORD_RE.finditer(text):
        anchored = True
        _add(match.group(1))
        # Harvest "6.3 and 6.4" siblings immediately following this match.
        tail = text[match.end() : match.end() + 80]
        for sib in _CLAUSE_LIST_RE.finditer(tail):
            _add(sib.group(1))

    for match in _CLAUSE_PHRASE_RE.finditer(text):
        anchored = True
        _add(match.group(1))
        tail = text[match.end() : match.end() + 80]
        for sib in _CLAUSE_LIST_RE.finditer(tail):
            _add(sib.group(1))

    if not anchored:
        return []
    return out


def normalize_designation(value: str) -> str:
    """Lowercase and collapse whitespace in a standard designation."""

    return _WS_RE.sub(" ", (value or "").strip().lower())


def mentions_designation(text: str, designation: str) -> bool:
    """True when ``designation`` appears in ``text`` as a whole token run.

    Matching is case-insensitive and whitespace-insensitive, with boundaries
    that forbid a partial hit inside a longer identifier (so "EN 1990" does not
    match inside "EN 19900"). Used to detect when one standard's text references
    another standard by its designation.

    >>> mentions_designation("Loads per EN 1990 apply.", "EN 1990")
    True
    >>> mentions_designation("Loads per EN 19900 apply.", "EN 1990")
    False
    """

    desig = normalize_designation(designation)
    if not desig:
        return False
    haystack = normalize_designation(text)
    if not haystack:
        return False
    pattern = r"(?<![\w-])" + re.escape(desig) + r"(?![\w-])"
    return re.search(pattern, haystack) is not None


__all__ = [
    "find_clause_references",
    "normalize_designation",
    "mentions_designation",
]
