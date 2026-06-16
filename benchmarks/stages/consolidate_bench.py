"""Consolidate stage benchmark.

Quality measures whether the cross-document entity resolution produced the
expected knowledge graph:

* ``object_f1`` - precision/recall of the consolidated object ids versus gold;
* ``merge_accuracy`` - the fraction of objects that gathered mentions from at
  least the expected number of distinct documents (i.e. near-duplicate surface
  forms across documents collapsed into one object, and nothing over-merged);
* ``relationship_recall`` - the fraction of expected object-to-object
  relationships that were resolved and persisted.

Performance is consolidation throughput in candidate mentions per second.
"""

from __future__ import annotations

from catalog.db import connect
from catalog.knowledge.service import consolidate

from ..metrics import StageResult, Timer, fraction, normalized_prf1, performance


def _read_graph(db_path: str):
    with connect(db_path) as conn:
        object_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
        mention_rows = conn.execute(
            "SELECT knowledge_object_id, artifact_id FROM knowledge_mentions"
        ).fetchall()
        rel_rows = conn.execute(
            "SELECT source_object, predicate, target_object FROM knowledge_relationships"
        ).fetchall()

    docs_per_object: dict[str, set[str]] = {}
    for row in mention_rows:
        docs_per_object.setdefault(row["knowledge_object_id"], set()).add(row["artifact_id"])
    relationships = {
        f"{r['source_object']}::{r['predicate']}::{r['target_object']}" for r in rel_rows
    }
    return object_ids, docs_per_object, relationships


def run(ctx) -> StageResult:
    result = StageResult(stage="consolidate")
    try:
        with Timer() as t:
            stats = consolidate(ctx.db_path)

        object_ids, docs_per_object, relationships = _read_graph(ctx.db_path)
        gold = ctx.corpus.consolidate

        gold_object_ids = [o["id"] for o in gold["objects"]]
        object_scores = normalized_prf1(object_ids, gold_object_ids)

        merge_correct = 0
        for obj in gold["objects"]:
            distinct_docs = len(docs_per_object.get(obj["id"], set()))
            if distinct_docs >= obj.get("min_documents", 1):
                merge_correct += 1

        gold_rels = [
            f"{r['source_object']}::{r['predicate']}::{r['target_object']}"
            for r in gold["relationships"]
        ]
        rel_scores = normalized_prf1(relationships, gold_rels)

        result.quality = {
            "object_precision": object_scores["precision"],
            "object_recall": object_scores["recall"],
            "object_f1": object_scores["f1"],
            "merge_accuracy": fraction(merge_correct, len(gold["objects"])),
            "relationship_recall": rel_scores["recall"],
            "objects_created": stats.objects_created,
        }
        result.performance = performance(stats.mentions_gathered, t.seconds)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    return result


__all__ = ["run"]
