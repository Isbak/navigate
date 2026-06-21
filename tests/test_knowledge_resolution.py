"""Unit tests for entity resolution, scoring, and merge suggestions.

These exercise the pure pieces of the knowledge layer without a database: name
normalization, similarity, clustering (including the LLM-assisted merge hook),
duplicate-candidate detection, and confidence scoring.
"""

from catalog.knowledge.ids import object_id, slugify
from catalog.knowledge.models import RawMention
from catalog.knowledge.prompts import (
    build_merge_prompt,
    make_merge_judge,
    parse_merge_answer,
)
from catalog.knowledge.resolution import (
    ResolutionConfig,
    cluster_mentions,
    cross_type_duplicate_pairs,
    duplicate_candidate_pairs,
    normalize_name,
    similarity,
)
from catalog.knowledge.scoring import ScoringInputs, score_object
from catalog.semantic.providers.base import BaseLLMProvider, LLMError


def _mention(name, artifact, object_type="Capability", confidence=0.9, text="quote"):
    return RawMention(object_type, name, artifact, confidence, text)


# -- ids ----------------------------------------------------------------------

def test_stable_uri_ready_ids():
    assert object_id("Capability", "Release Governance") == "capability_release_governance"
    assert object_id("Platform", "Salesforce") == "platform_salesforce"
    assert object_id("Decision", "Launchpad Model") == "decision_launchpad_model"
    assert slugify("  Weird/Name!! ") == "weird_name"
    assert slugify("???") == "unnamed"


# -- normalization + similarity ----------------------------------------------

def test_normalize_collapses_case_and_punctuation():
    assert normalize_name("Release Governance") == "release governance"
    assert normalize_name("Release governance!") == "release governance"
    assert normalize_name("  Release   Governance  ") == "release governance"


def test_similarity_exact_after_normalization():
    assert similarity("Release Governance", "Release governance") == 1.0


def test_similarity_containment_auto_merges():
    # "X" vs "X Model" must clear the default auto-merge threshold.
    score = similarity("Release Governance", "Release Governance Model")
    assert score >= ResolutionConfig().auto_merge_threshold


def test_similarity_distinguishes_different_things():
    assert similarity("Release Governance", "Salesforce Platform") < 0.3
    assert similarity("Release Management", "Release Governance") < 0.72


def test_lone_generic_token_does_not_auto_merge_into_specific_name():
    # A single generic word must NOT be lifted into a specific multi-word name by
    # the containment boost (the over-merge guard): "Governance" is its own thing.
    assert similarity("Governance", "Release Governance") < (
        ResolutionConfig().auto_merge_threshold
    )
    assert similarity("Data", "Data Platform") < (
        ResolutionConfig().auto_merge_threshold
    )
    # But multi-token containment still merges ("X Y" vs "X Y Z").
    assert similarity("Customer Data", "Customer Data Platform") >= (
        ResolutionConfig().auto_merge_threshold
    )


# -- clustering / entity resolution ------------------------------------------

def test_three_variants_collapse_to_one_object():
    mentions = [
        _mention("Release Governance", "doc_a"),
        _mention("Release Governance", "doc_b"),
        _mention("Release Governance Model", "doc_c"),
        _mention("Release governance", "doc_d"),
    ]
    clusters = cluster_mentions(mentions)
    assert len(clusters) == 1
    cluster = clusters[0]
    # Canonical name prefers the most common, most concise surface form.
    assert cluster.canonical_name == "Release Governance"
    assert cluster.artifact_ids == {"doc_a", "doc_b", "doc_c", "doc_d"}


def test_different_types_never_merge():
    mentions = [
        _mention("Salesforce", "doc_a", object_type="Platform"),
        _mention("Salesforce", "doc_b", object_type="Technology"),
    ]
    clusters = cluster_mentions(mentions)
    assert len(clusters) == 2


def test_low_confidence_mentions_filtered_out():
    mentions = [
        _mention("Strong", "doc_a", confidence=0.9),
        _mention("Weak", "doc_b", confidence=0.1),
    ]
    config = ResolutionConfig(min_mention_confidence=0.5)
    clusters = cluster_mentions(mentions, config)
    names = {c.canonical_name for c in clusters}
    assert names == {"Strong"}


def test_default_confidence_floor_drops_noise():
    # The default config now carries a non-zero floor, so weak one-off mentions
    # do not each become a knowledge object out of the box.
    assert ResolutionConfig().min_mention_confidence > 0.0
    clusters = cluster_mentions(
        [
            _mention("Real Capability", "doc_a", confidence=0.9),
            _mention("Noise", "doc_b", confidence=0.1),
        ]
    )
    assert {c.canonical_name for c in clusters} == {"Real Capability"}


def test_merge_confidence_recorded():
    # Exact group -> cohesion 1.0; a fuzzy (containment) merge -> < 1.0.
    exact = cluster_mentions(
        [_mention("Release Governance", "a"), _mention("Release governance", "b")]
    )
    assert exact[0].merge_confidence == 1.0

    fuzzy = cluster_mentions(
        [
            _mention("Customer Data Platform", "a"),
            _mention("Customer Data Platform Analytics", "b"),
        ]
    )
    assert len(fuzzy) == 1
    assert fuzzy[0].merge_confidence < 1.0


# -- LLM-assisted merge suggestions ------------------------------------------

def test_merge_judge_promotes_borderline_pair():
    # A pair in the review band: similar enough to ask about, not to auto-merge.
    a, b = "Customer Portal", "Customer Dashboard"
    config = ResolutionConfig(auto_merge_threshold=0.95, review_threshold=0.3)
    assert config.review_threshold <= similarity(a, b) < config.auto_merge_threshold

    calls = []

    def judge(x, y, t):
        calls.append((x, y, t))
        return True

    merged = cluster_mentions(
        [_mention(a, "doc_a"), _mention(b, "doc_b")], config, merge_judge=judge
    )
    assert len(merged) == 1
    assert calls  # the judge was actually consulted


def test_merge_judge_can_decline():
    config = ResolutionConfig(auto_merge_threshold=0.95, review_threshold=0.3)
    merged = cluster_mentions(
        [_mention("Customer Portal", "a"), _mention("Customer Dashboard", "b")],
        config,
        merge_judge=lambda x, y, t: False,
    )
    assert len(merged) == 2


# -- duplicate candidates -----------------------------------------------------

def test_duplicate_candidates_surface_borderline_pairs():
    objects = [
        ("a", "Capability", "Customer Portal"),
        ("b", "Capability", "Customer Dashboard"),
        ("c", "Capability", "Totally Unrelated Thing"),
    ]
    config = ResolutionConfig(auto_merge_threshold=0.95, review_threshold=0.3)
    pairs = duplicate_candidate_pairs(objects, config)
    names = {(p["left_name"], p["right_name"]) for p in pairs}
    assert ("Customer Portal", "Customer Dashboard") in names
    # The unrelated object is not similar enough to suggest as a duplicate.
    flat = {name for pair in names for name in pair}
    assert "Totally Unrelated Thing" not in flat


def test_cross_type_duplicates_surface_same_name_different_type():
    objects = [
        ("a", "Capability", "Release Governance"),
        ("b", "Concept", "Release governance"),  # same name, different type
        ("c", "Capability", "Release Governance"),  # same type -> not surfaced
        ("d", "Platform", "Salesforce"),
    ]
    pairs = cross_type_duplicate_pairs(objects)
    typed = {(p["left_type"], p["right_type"]) for p in pairs}
    assert {"Capability", "Concept"} == set().union(*typed)
    # Salesforce appears once, so it is never surfaced as a cross-type collision.
    assert all("Salesforce" not in (p["left_name"], p["right_name"]) for p in pairs)
    # Two same-type Capabilities with the same name are not a cross-type pair.
    assert all(p["left_type"] != p["right_type"] for p in pairs)


# -- scoring ------------------------------------------------------------------

def test_score_rewards_breadth_and_clamps():
    broad = score_object(
        ScoringInputs(
            mention_confidences=[0.9] * 27,
            document_count=27,
            mention_count=27,
        )
    )
    narrow = score_object(
        ScoringInputs(
            mention_confidences=[0.9],
            document_count=1,
            mention_count=1,
        )
    )
    assert broad > narrow
    assert 0.0 <= broad <= 1.0
    # 27 documents of strong, consistent mentions should be highly trusted.
    assert broad >= 0.9


def test_score_penalizes_rejected_relationships():
    consistent = score_object(
        ScoringInputs([0.9] * 5, 5, 5, relationship_total=4, relationship_rejected=0)
    )
    contradicted = score_object(
        ScoringInputs([0.9] * 5, 5, 5, relationship_total=4, relationship_rejected=4)
    )
    assert contradicted < consistent


def test_score_reflects_review_history():
    base_inputs = ScoringInputs([0.7] * 3, 3, 3)
    approved = score_object(
        ScoringInputs([0.7] * 3, 3, 3, review_actions=("APPROVED",))
    )
    rejected = score_object(
        ScoringInputs([0.7] * 3, 3, 3, review_actions=("REJECTED",))
    )
    neutral = score_object(base_inputs)
    assert approved > neutral > rejected


# -- merge suggestion prompt --------------------------------------------------

def test_parse_merge_answer_is_conservative():
    assert parse_merge_answer("YES") is True
    assert parse_merge_answer("yes, the same") is True
    assert parse_merge_answer("NO") is False
    assert parse_merge_answer("No, different scope") is False
    assert parse_merge_answer("") is False
    assert parse_merge_answer("<think>maybe</think> NO") is False


def test_build_merge_prompt_mentions_both_names():
    prompt = build_merge_prompt("Alpha", "Beta", "Capability")
    assert "Alpha" in prompt and "Beta" in prompt and "Capability" in prompt


def test_make_merge_judge_swallows_provider_errors():
    class BoomProvider(BaseLLMProvider):
        def generate(self, prompt, *, system=None):
            raise LLMError("model down")

    judge = make_merge_judge(BoomProvider("m"))
    # A failing model degrades to "do not merge", never raises.
    assert judge("Alpha", "Beta", "Capability") is False
