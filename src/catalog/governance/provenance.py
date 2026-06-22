"""Reviewer provenance for agent-made review decisions.

Agent approvals reuse the same ``APPROVED`` state as human ones, but are tagged
in the ``reviewer`` column so they stay distinguishable, filterable, and
reversible. The convention is a single ``agent:`` prefix on the reviewer string;
these helpers are the one place that encodes it.
"""

from __future__ import annotations

AGENT_PREFIX = "agent:"


def agent_reviewer(name: str) -> str:
    """Build the ``reviewer`` string for an agent identity (``agent:<name>``)."""

    name = (name or "agent").strip() or "agent"
    if name.startswith(AGENT_PREFIX):
        return name
    return f"{AGENT_PREFIX}{name}"


def is_agent_reviewer(reviewer: str | None) -> bool:
    """True if a ``reviewer`` string denotes an agent rather than a human."""

    return reviewer is not None and reviewer.startswith(AGENT_PREFIX)


__all__ = ["AGENT_PREFIX", "agent_reviewer", "is_agent_reviewer"]
