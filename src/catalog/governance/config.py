"""Configuration for the governance layer.

Loads ``config/governance.yml`` into typed dataclasses, falling back to sensible
defaults for any missing file or key so governance always runs. The defaults
encode the rules named in the spec (180 days -> AGING, 365 days -> STALE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FreshnessConfig:
    aging_days: int = 180
    stale_days: int = 365
    archived_days: int = 730


@dataclass(frozen=True)
class ReviewConfig:
    stale_review_days: int = 365


@dataclass(frozen=True)
class QualityConfig:
    weight_evidence: float = 0.20
    weight_review: float = 0.20
    weight_freshness: float = 0.15
    weight_consistency: float = 0.15
    weight_owner: float = 0.10
    weight_confidence: float = 0.20
    target_evidence: int = 5
    low_quality_threshold: float = 60.0


@dataclass(frozen=True)
class DriftConfig:
    evidence_drop_ratio: float = 0.5
    terminology_min_documents: int = 5
    min_confidence_delta: float = 0.05


@dataclass(frozen=True)
class DomainConfig:
    name: str
    owner: str = ""


@dataclass(frozen=True)
class IngestionConfig:
    schedule: str = "manual"


_DEFAULT_DOMAINS = (
    DomainConfig("Digital Transformation", "Digital Platform Team"),
    DomainConfig("Architecture", "Architecture Guild"),
    DomainConfig("Leadership", "Executive Team"),
    DomainConfig("Test & Release", "Test & Release Team"),
    DomainConfig("Data", "Data Team"),
    DomainConfig("Operations", "Operations Team"),
)


@dataclass(frozen=True)
class GovernanceConfig:
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    drift: DriftConfig = field(default_factory=DriftConfig)
    domains: tuple[DomainConfig, ...] = _DEFAULT_DOMAINS
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)

    def domain_owner(self, name: str) -> str:
        for domain in self.domains:
            if domain.name.lower() == (name or "").lower():
                return domain.owner
        return ""


def _section(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def load_governance_config(
    path: str | Path = "config/governance.yml",
) -> GovernanceConfig:
    """Load governance config, returning all-defaults if the file is absent."""

    p = Path(path)
    if not p.exists():
        return GovernanceConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return GovernanceConfig()

    fr = _section(data, "freshness")
    rv = _section(data, "review")
    qc = _section(data, "quality")
    weights = _section(qc, "weights")
    dr = _section(data, "drift")
    ing = _section(data, "ingestion")

    domains_raw = data.get("domains")
    if isinstance(domains_raw, list) and domains_raw:
        domains = tuple(
            DomainConfig(name=str(d.get("name", "")).strip(), owner=str(d.get("owner", "")).strip())
            for d in domains_raw
            if isinstance(d, dict) and str(d.get("name", "")).strip()
        )
    else:
        domains = _DEFAULT_DOMAINS

    defaults = QualityConfig()
    return GovernanceConfig(
        freshness=FreshnessConfig(
            aging_days=int(fr.get("aging_days", 180)),
            stale_days=int(fr.get("stale_days", 365)),
            archived_days=int(fr.get("archived_days", 730)),
        ),
        review=ReviewConfig(
            stale_review_days=int(rv.get("stale_review_days", 365)),
        ),
        quality=QualityConfig(
            weight_evidence=float(weights.get("evidence", defaults.weight_evidence)),
            weight_review=float(weights.get("review", defaults.weight_review)),
            weight_freshness=float(weights.get("freshness", defaults.weight_freshness)),
            weight_consistency=float(weights.get("consistency", defaults.weight_consistency)),
            weight_owner=float(weights.get("owner", defaults.weight_owner)),
            weight_confidence=float(weights.get("confidence", defaults.weight_confidence)),
            target_evidence=int(qc.get("target_evidence", defaults.target_evidence)),
            low_quality_threshold=float(
                qc.get("low_quality_threshold", defaults.low_quality_threshold)
            ),
        ),
        drift=DriftConfig(
            evidence_drop_ratio=float(dr.get("evidence_drop_ratio", 0.5)),
            terminology_min_documents=int(dr.get("terminology_min_documents", 5)),
            min_confidence_delta=float(dr.get("min_confidence_delta", 0.05)),
        ),
        domains=domains,
        ingestion=IngestionConfig(schedule=str(ing.get("schedule", "manual"))),
    )


__all__ = [
    "FreshnessConfig",
    "ReviewConfig",
    "QualityConfig",
    "DriftConfig",
    "DomainConfig",
    "IngestionConfig",
    "GovernanceConfig",
    "load_governance_config",
]
