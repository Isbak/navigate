"""CI gate for the pipeline benchmark suite.

Runs the full scan/extract/classify/consolidate/ask benchmark in deterministic
(stub) mode against the in-repo gold corpus and asserts every stage meets its
quality thresholds. Performance numbers are recorded by the harness but never
gated here (CI-runner timing variance).
"""

from __future__ import annotations

import pytest
from benchmarks.runner import _load_thresholds, run_suite
from benchmarks.stages import STAGE_ORDER


@pytest.fixture(scope="module")
def suite_results():
    """Run the suite once and index results by stage for the whole module."""

    results = run_suite(provider="stub", thresholds=_load_thresholds(None))
    return {r.stage: r for r in results}


def test_all_stages_present(suite_results):
    assert set(suite_results) == set(STAGE_ORDER)


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_stage_has_no_error(suite_results, stage):
    result = suite_results[stage]
    assert result.error is None, f"{stage} errored: {result.error}"


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_stage_meets_thresholds(suite_results, stage):
    result = suite_results[stage]
    assert result.passed, f"{stage} failed quality gate: {result.failures}"


def test_scan_delta_detection_is_exact(suite_results):
    assert suite_results["scan"].quality["delta_accuracy"] == 1.0
    assert suite_results["scan"].quality["duplicate_accuracy"] == 1.0


def test_classify_recovers_document_types(suite_results):
    # In stub mode the provider returns gold, so persistence must be faithful.
    assert suite_results["classify"].quality["document_type_accuracy"] == 1.0


def test_consolidate_merges_cross_document_duplicates(suite_results):
    # "Release Governance" + "Release governance" collapse into one object, etc.
    assert suite_results["consolidate"].quality["merge_accuracy"] == 1.0
    assert suite_results["consolidate"].quality["object_f1"] >= 0.9


def test_ask_declines_when_unsupported(suite_results):
    # Groundedness includes correctly declining an unanswerable question.
    assert suite_results["ask"].quality["groundedness_accuracy"] == 1.0


def test_performance_is_recorded(suite_results):
    for stage in STAGE_ORDER:
        perf = suite_results[stage].performance
        assert "items_per_sec" in perf
