"""Configuration for the compliance layer.

Loads ``config/compliance.yml`` into a typed dataclass, falling back to sensible
defaults for any missing file or key so compliance always runs. The defaults
encode the assessment rules: which knowledge-object types may act as controls,
the coverage threshold a standard must clear to be considered "covered", and the
evidence-staleness horizon beyond which a SATISFIED claim is downgraded to
PARTIAL (stale proof is weaker proof).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Knowledge-object types that may satisfy a requirement by default - the things
# an organization actually operates as controls.
_DEFAULT_CONTROL_TYPES = ("Capability", "Process", "Platform", "Technology")


@dataclass(frozen=True)
class ComplianceConfig:
    control_types: tuple[str, ...] = _DEFAULT_CONTROL_TYPES
    coverage_threshold: float = 0.8
    stale_evidence_days: int = 365
    require_approved_controls: bool = True

    def __post_init__(self) -> None:  # normalize types for membership tests
        object.__setattr__(
            self, "control_types", tuple(str(t) for t in self.control_types)
        )


def load_compliance_config(
    path: str | Path = "config/compliance.yml",
) -> ComplianceConfig:
    """Load compliance config, returning all-defaults if the file is absent."""

    p = Path(path)
    if not p.exists():
        return ComplianceConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return ComplianceConfig()

    defaults = ComplianceConfig()
    control_types = data.get("control_types")
    if not isinstance(control_types, list) or not control_types:
        control_types = list(defaults.control_types)
    assessment = data.get("assessment") if isinstance(data.get("assessment"), dict) else {}
    return ComplianceConfig(
        control_types=tuple(str(t) for t in control_types),
        coverage_threshold=float(
            assessment.get("coverage_threshold", defaults.coverage_threshold)
        ),
        stale_evidence_days=int(
            assessment.get("stale_evidence_days", defaults.stale_evidence_days)
        ),
        require_approved_controls=bool(
            assessment.get("require_approved_controls", defaults.require_approved_controls)
        ),
    )


__all__ = ["ComplianceConfig", "load_compliance_config"]
