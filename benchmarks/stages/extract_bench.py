"""Extract stage benchmark.

Quality has two parts, measured from the per-artifact cache that extraction
writes (``cache/<id>/extracted.txt`` and ``links.json``):

* **text recall** - the fraction of the gold "must-contain" markers that appear
  in the extracted text;
* **link F1** - precision/recall of the raw URLs extracted versus the gold set.

Performance is extraction throughput in documents per second.
"""

from __future__ import annotations

import json
from pathlib import Path

from catalog.db import connect
from catalog.extraction import extract_all

from ..metrics import StageResult, Timer, mean, normalized_prf1, performance


def _filename_to_id(db_path: str) -> dict[str, str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename FROM artifacts WHERE scan_status != 'DELETED'"
        ).fetchall()
    return {row["filename"]: row["id"] for row in rows}


def run(ctx) -> StageResult:
    result = StageResult(stage="extract")
    try:
        with Timer() as t:
            stats = extract_all(ctx.db_path, ctx.cache_dir)

        cache = Path(ctx.cache_dir)
        name_to_id = _filename_to_id(ctx.db_path)
        gold = ctx.corpus.extract

        text_recalls: list[float] = []
        predicted_links: set[str] = set()
        gold_links: set[str] = set()

        for filename, expected in gold.items():
            artifact_id = name_to_id.get(filename)
            if artifact_id is None:
                text_recalls.append(0.0)
                gold_links.update(expected.get("links", []))
                continue
            adir = cache / artifact_id
            text = (adir / "extracted.txt").read_text(encoding="utf-8")
            markers = expected.get("text_markers", [])
            found = sum(1 for m in markers if m.lower() in text.lower())
            text_recalls.append(found / len(markers) if markers else 1.0)

            links_raw = json.loads((adir / "links.json").read_text(encoding="utf-8"))
            predicted_links.update(
                f"{filename}::{link['raw_url']}" for link in links_raw
            )
            gold_links.update(f"{filename}::{u}" for u in expected.get("links", []))

        link_scores = normalized_prf1(predicted_links, gold_links)
        result.quality = {
            "text_recall": mean(text_recalls),
            "link_precision": link_scores["precision"],
            "link_recall": link_scores["recall"],
            "link_f1": link_scores["f1"],
            "links_extracted": stats["links_extracted"],
        }
        result.performance = performance(stats["artifacts_processed"], t.seconds)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    return result


__all__ = ["run"]
