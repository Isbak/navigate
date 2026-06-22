"""Unit tests for the deterministic cross-reference text scanners."""

from catalog.knowledge.references import (
    find_clause_references,
    mentions_designation,
)


def test_keyword_clause_reference():
    assert find_clause_references("Design in accordance with clause 6.2.") == ["6.2"]


def test_reference_verb_with_dotted_number():
    assert find_clause_references("See 7.3.1 for partial factors.") == ["7.3.1"]


def test_clause_list_siblings_are_harvested():
    text = "Apply the rules of clauses 6.2, 6.3 and 6.4 together."
    assert find_clause_references(text) == ["6.2", "6.3", "6.4"]


def test_bare_number_without_anchor_is_ignored():
    # A value that happens to look like a clause but has no reference context.
    assert find_clause_references("The factor is 1.5 and the load is 2.0.") == []


def test_single_number_needs_keyword_not_just_verb():
    # "see 5" alone is too weak; a verb form needs a dotted number.
    assert find_clause_references("see 5 below") == []
    # But a keyword form may carry a single number.
    assert find_clause_references("see section 5 below") == ["5"]


def test_duplicates_collapse_in_order():
    text = "Per clause 6.2; again clause 6.2 and clause 6.5."
    assert find_clause_references(text) == ["6.2", "6.5"]


def test_mentions_designation_whole_token_only():
    assert mentions_designation("Loads per EN 1990 apply.", "EN 1990") is True
    assert mentions_designation("Loads per EN 19900 apply.", "EN 1990") is False


def test_mentions_designation_is_whitespace_and_case_insensitive():
    assert mentions_designation("see  en   1990 here", "EN 1990") is True


def test_mentions_designation_preserves_hyphens():
    assert mentions_designation("Refer to EN 1992-1-1.", "EN 1992-1-1") is True
    assert mentions_designation("Refer to EN 1992.", "EN 1992-1-1") is False
