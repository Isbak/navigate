"""Classify stage benchmark.

Quality compares the persisted semantic proposals against the gold corpus:

* ``document_type_accuracy`` - exact label match on the controlled vocabulary;
* set-based F1 for domains, capabilities, entities, decisions, risks, and
  relationships.

In deterministic (stub) mode the provider returns the gold response, so these
scores validate the *parser + persistence* path and give a stable ~1.0 baseline;
with ``--provider`` they measure a real model's accuracy against the same gold.

Performance is classification throughput in documents per second (which, in real
mode, includes the LLM round-trip).
"""

from __future__ import annotations

import json

from catalog.db import connect
from catalog.semantic.service import classify_documents

from ..metrics import StageResult, Timer, label_accuracy, normalized_prf1, performance


def _candidates_by_artifact(db_path: str) -> dict:
    """Read every persisted candidate row, grouped by artifact id."""

    out: dict[str, dict] = {}

    def bucket(artifact_id: str) -> dict:
        return out.setdefault(
            artifact_id,
            {
                "document_type": None,
                "domains": [],
                "capabilities": [],
                "entities": [],
                "decisions": [],
                "risks": [],
                "relationships": [],
            },
        )

    with connect(db_path) as conn:
        for row in conn.execute(
            "SELECT artifact_id, document_type, domains FROM document_classifications"
        ):
            b = bucket(row["artifact_id"])
            b["document_type"] = row["document_type"]
            b["domains"] = [d.get("domain", "") for d in json.loads(row["domains"] or "[]")]
        for row in conn.execute("SELECT artifact_id, name FROM candidate_capabilities"):
            bucket(row["artifact_id"])["capabilities"].append(row["name"])
        for row in conn.execute(
            "SELECT artifact_id, entity_type, name FROM candidate_entities"
        ):
            bucket(row["artifact_id"])["entities"].append(
                f"{row['entity_type']}::{row['name']}"
            )
        for row in conn.execute("SELECT artifact_id, decision_text FROM candidate_decisions"):
            bucket(row["artifact_id"])["decisions"].append(row["decision_text"])
        for row in conn.execute("SELECT artifact_id, risk_description FROM candidate_risks"):
            bucket(row["artifact_id"])["risks"].append(row["risk_description"])
        for row in conn.execute(
            "SELECT artifact_id, subject, predicate, object FROM candidate_relationships"
        ):
            bucket(row["artifact_id"])["relationships"].append(
                f"{row['subject']}::{row['predicate']}::{row['object']}"
            )
    return out


def _filename_to_id(db_path: str) -> dict[str, str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename FROM artifacts WHERE scan_status != 'DELETED'"
        ).fetchall()
    return {row["filename"]: row["id"] for row in rows}


def _gold_entities(entry: dict) -> list[str]:
    return [f"{e['entity_type']}::{e['name']}" for e in entry.get("entities", [])]


def _gold_relationships(entry: dict) -> list[str]:
    return [
        f"{r['subject']}::{r['predicate']}::{r['object']}"
        for r in entry.get("relationships", [])
    ]


def run(ctx) -> StageResult:
    result = StageResult(stage="classify")
    try:
        with Timer() as t:
            stats = classify_documents(
                ctx.db_path, ctx.cache_dir, ctx.classify_provider, force=True
            )

        name_to_id = _filename_to_id(ctx.db_path)
        persisted = _candidates_by_artifact(ctx.db_path)
        gold = ctx.corpus.classify

        type_pairs: list[tuple[str, str]] = []
        # Filename-scoped global sets keep per-document boundaries while letting
        # us compute one corpus-level F1 per field. Each field maps to its
        # threshold metric key.
        field_metric = {
            "domains": "domain_f1",
            "capabilities": "capability_f1",
            "entities": "entity_f1",
            "decisions": "decision_f1",
            "risks": "risk_f1",
            "relationships": "relationship_f1",
        }
        fields = list(field_metric)
        pred_sets = {f: set() for f in fields}
        gold_sets = {f: set() for f in fields}

        for filename, entry in gold.items():
            artifact_id = name_to_id.get(filename, filename)
            got = persisted.get(artifact_id, {})

            type_pairs.append((got.get("document_type") or "MISSING", entry["document_type"]))

            scoped = {
                "domains": ([d.get("domain", "") for d in entry.get("domains", [])],
                            got.get("domains", [])),
                "capabilities": ([c["name"] for c in entry.get("capabilities", [])],
                                 got.get("capabilities", [])),
                "entities": (_gold_entities(entry), got.get("entities", [])),
                "decisions": ([d["decision_text"] for d in entry.get("decisions", [])],
                              got.get("decisions", [])),
                "risks": ([r["risk_description"] for r in entry.get("risks", [])],
                          got.get("risks", [])),
                "relationships": (_gold_relationships(entry), got.get("relationships", [])),
            }
            for field, (g_items, p_items) in scoped.items():
                gold_sets[field].update(f"{filename}::{x}" for x in g_items)
                pred_sets[field].update(f"{filename}::{x}" for x in p_items)

        result.quality = {"document_type_accuracy": label_accuracy(type_pairs)}
        for field in fields:
            scores = normalized_prf1(pred_sets[field], gold_sets[field])
            result.quality[field_metric[field]] = scores["f1"]
        result.quality["documents_processed"] = stats.documents_processed

        result.performance = performance(stats.documents_processed, t.seconds)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    return result


__all__ = ["run"]
