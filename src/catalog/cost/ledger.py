"""The seam services use to record what each LLM call cost.

A :class:`UsageLedger` reads the :class:`~catalog.cost.usage.Usage` a provider
exposes after ``generate()`` (or takes one directly), prices it, and writes an
``llm_usage`` row. :class:`NullUsageLedger` is the no-op stand-in so callers can
record unconditionally - a stub provider (which never sets ``last_usage``) or a
missing ledger simply records nothing, keeping existing tests green.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from . import repository
from .pricing import PricingTable, compute_cost, load_pricing
from .usage import Usage


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class UsageLedger:
    """Records priced ``llm_usage`` rows onto an open connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        pricing: PricingTable,
        *,
        provider_name: str | None = None,
        run_id: int | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.conn = conn
        self.pricing = pricing
        self.provider_name = provider_name
        self.run_id = run_id
        self._now = now or _utc_now

    def record(self, provider, *, operation: str, artifact_id: str | None = None) -> None:
        """Record the provider's most recent call, if it reported usage."""

        usage = getattr(provider, "last_usage", None)
        if usage is None:
            return
        self.record_usage(usage, operation=operation, artifact_id=artifact_id)

    def record_usage(
        self, usage: Usage, *, operation: str, artifact_id: str | None = None
    ) -> None:
        repository.record_usage(
            self.conn,
            operation=operation,
            model=usage.model,
            provider=self.provider_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            latency_ms=usage.latency_ms,
            cost_usd=compute_cost(usage, self.pricing),
            artifact_id=artifact_id,
            run_id=self.run_id,
            created_at=self._now(),
        )


class NullUsageLedger:
    """A ledger that records nothing. Used when cost tracking is disabled."""

    def record(self, provider, *, operation: str, artifact_id: str | None = None) -> None:
        return None

    def record_usage(
        self, usage: Usage, *, operation: str, artifact_id: str | None = None
    ) -> None:
        return None


def null_ledger() -> NullUsageLedger:
    return NullUsageLedger()


def record_calls(
    db_path: str | Path,
    usages: Iterable[Usage | None],
    *,
    operation: str,
    provider_name: str | None = None,
    artifact_id: str | None = None,
    pricing: PricingTable | None = None,
) -> int:
    """Persist a batch of already-collected usages in one transaction.

    Used by call sites that accumulate :class:`Usage` during an operation (vision
    extraction, GraphRAG ask, merge judging) and flush them afterwards, rather
    than holding a connection open across the whole operation. Returns the number
    of rows written. A no-op when there is nothing to record.
    """

    collected = [u for u in usages if u is not None]
    if not collected:
        return 0

    from ..db import connect, init_db  # local import avoids an import cycle

    table = pricing if pricing is not None else load_pricing()
    init_db(db_path)
    with connect(db_path) as conn:
        ledger = UsageLedger(conn, table, provider_name=provider_name)
        for usage in collected:
            ledger.record_usage(usage, operation=operation, artifact_id=artifact_id)
        conn.commit()
    return len(collected)


__all__ = [
    "UsageLedger",
    "NullUsageLedger",
    "null_ledger",
    "record_calls",
]
