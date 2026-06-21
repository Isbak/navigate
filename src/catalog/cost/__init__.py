"""Token-usage and cost accounting for LLM calls.

Every provider call (classification chunk, vision page, GraphRAG answer, merge
judge) returns token-usage figures that the text-only ``generate() -> str``
contract used to discard. This package captures that usage, prices it from
``config/pricing.yml``, and persists one row per call to the ``llm_usage`` table
so the cost of extraction can be measured and reported (``catalog cost-report``).

The package imports nothing from the provider layer, keeping providers
vendor-agnostic; providers only expose a passive :attr:`last_usage` attribute the
ledger reads after each call.
"""

from __future__ import annotations

from .ledger import NullUsageLedger, UsageLedger, null_ledger, record_calls
from .pricing import ModelRate, PricingTable, compute_cost, load_pricing
from .usage import Usage

__all__ = [
    "Usage",
    "ModelRate",
    "PricingTable",
    "load_pricing",
    "compute_cost",
    "UsageLedger",
    "NullUsageLedger",
    "null_ledger",
    "record_calls",
]
