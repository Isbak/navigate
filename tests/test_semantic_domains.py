"""Tests for domain canonicalization (confidence floor, mapping, fuzzy merge)."""

from __future__ import annotations

import textwrap

from catalog.semantic.domains import (
    DomainTaxonomy,
    canonicalize_domains,
    load_domain_taxonomy,
)
from catalog.semantic.models import DomainScore


def _names(domains: list[DomainScore]) -> list[str]:
    return [d.domain for d in domains]


def test_missing_file_yields_permissive_defaults(tmp_path):
    taxonomy = load_domain_taxonomy(tmp_path / "nope.yml")
    assert taxonomy.min_confidence == 0.6
    assert taxonomy.allow_unlisted is True
    assert taxonomy.canonical == ()
    assert taxonomy.aliases == {}


def test_load_taxonomy_builds_alias_map(tmp_path):
    path = tmp_path / "domains.yml"
    path.write_text(
        textwrap.dedent(
            """
            min_confidence: 0.5
            canonical:
              - name: "Structural Engineering"
                aliases: ["Steel Design", "Steel Construction"]
            """
        ),
        encoding="utf-8",
    )
    taxonomy = load_domain_taxonomy(path)
    assert taxonomy.min_confidence == 0.5
    assert taxonomy.canonical == ("Structural Engineering",)
    # Each alias and the canonical name itself resolve via the normalized key.
    assert taxonomy.aliases["steel design"] == "Structural Engineering"
    assert taxonomy.aliases["steel construction"] == "Structural Engineering"
    assert taxonomy.aliases["structural engineering"] == "Structural Engineering"


def test_confidence_floor_drops_weak_domains():
    taxonomy = DomainTaxonomy(min_confidence=0.6)
    out = canonicalize_domains(
        [DomainScore("Architecture", 0.4), DomainScore("Quality Assurance", 0.7)],
        taxonomy,
    )
    assert _names(out) == ["Quality Assurance"]


def test_aliases_map_triplets_to_one_canonical():
    taxonomy = DomainTaxonomy(
        min_confidence=0.0,
        canonical=("Structural Engineering",),
        aliases={
            "steel design": "Structural Engineering",
            "steel construction": "Structural Engineering",
            "structural engineering": "Structural Engineering",
        },
    )
    out = canonicalize_domains(
        [
            DomainScore("Steel Design", 0.9),
            DomainScore("Steel Construction", 0.7),
            DomainScore("Structural Engineering", 0.8),
        ],
        taxonomy,
    )
    assert _names(out) == ["Structural Engineering"]
    # Confidence is the strongest seen across the merged synonyms.
    assert out[0].confidence == 0.9


def test_fuzzy_merge_collapses_near_duplicates_without_taxonomy():
    taxonomy = DomainTaxonomy(min_confidence=0.0, merge_threshold=0.88)
    out = canonicalize_domains(
        [
            DomainScore("Release Governance", 0.8),
            DomainScore("Release Governance Model", 0.6),
        ],
        taxonomy,
    )
    # The first surface form wins the display name; confidence is the max.
    assert _names(out) == ["Release Governance"]
    assert out[0].confidence == 0.8


def test_unlisted_dropped_when_not_allowed():
    taxonomy = DomainTaxonomy(
        min_confidence=0.0,
        allow_unlisted=False,
        canonical=("Structural Engineering",),
        aliases={"structural engineering": "Structural Engineering"},
    )
    out = canonicalize_domains(
        [
            DomainScore("Structural Engineering", 0.9),
            DomainScore("Cooking", 0.9),
        ],
        taxonomy,
    )
    assert _names(out) == ["Structural Engineering"]
