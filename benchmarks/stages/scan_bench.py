"""Scan stage benchmark.

Quality is the correctness of the scanner's delta detection: a fresh scan must
report every file as new (RAW), an unchanged re-scan must report them all
UNCHANGED, a content edit must surface as CHANGED, a new file as RAW, a removed
file as DELETED, and a byte-identical copy as DUPLICATE. We drive a throwaway
copy of the corpus so the mutations never touch the in-repo originals.

Performance is the throughput of the initial scan of the shared workspace, which
also seeds the database for every downstream stage.
"""

from __future__ import annotations

from pathlib import Path

from catalog.scanner import scan

from .. import corpus as corpus_mod
from ..metrics import StageResult, Timer, fraction, performance


def _delta_checks(workdir: Path) -> tuple[int, int, int, int]:
    """Run the add/modify/delete delta sequence; return (correct, total) pairs.

    Returns ``(delta_correct, delta_total, dup_correct, dup_total)``.
    """

    docs = corpus_mod.materialize_docs(workdir / "scan_docs")
    sources = corpus_mod.write_sources_yml(workdir / "scan_sources.yml", docs)
    db = str(workdir / "scan.sqlite")
    cache = str(workdir / "scan_cache")

    n_files = sum(1 for p in docs.iterdir() if p.is_file())

    delta_correct = 0
    delta_total = 0

    # 1. Fresh scan: everything is new.
    s1 = scan(sources, db, cache)
    delta_total += 1
    if s1.new_files == n_files and s1.changed_files == 0 and s1.deleted_files == 0:
        delta_correct += 1

    # 2. Unchanged re-scan: nothing new or changed.
    s2 = scan(sources, db, cache)
    delta_total += 1
    if s2.new_files == 0 and s2.changed_files == 0 and s2.unchanged_files == n_files:
        delta_correct += 1

    # 3. Modify one file: exactly one CHANGED.
    target = docs / "data_strategy.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nAppended line.\n", encoding="utf-8")
    s3 = scan(sources, db, cache)
    delta_total += 1
    if s3.changed_files == 1 and s3.new_files == 0:
        delta_correct += 1

    # 4. Add a new file: exactly one RAW.
    (docs / "new_note.md").write_text("# New Note\n\nFresh content.\n", encoding="utf-8")
    s4 = scan(sources, db, cache)
    delta_total += 1
    if s4.new_files == 1 and s4.changed_files == 0:
        delta_correct += 1

    # 5. Delete a file: exactly one DELETED.
    (docs / "new_note.md").unlink()
    s5 = scan(sources, db, cache)
    delta_total += 1
    if s5.deleted_files == 1:
        delta_correct += 1

    # 6. Duplicate detection: a byte-identical copy is flagged DUPLICATE.
    original = docs / "meeting_notes.md"
    (docs / "meeting_notes_copy.md").write_text(
        original.read_text(encoding="utf-8"), encoding="utf-8"
    )
    s6 = scan(sources, db, cache)
    dup_total = 1
    dup_correct = 1 if s6.duplicate_files >= 1 else 0

    return delta_correct, delta_total, dup_correct, dup_total


def run(ctx) -> StageResult:
    result = StageResult(stage="scan")
    try:
        # Quality: deterministic delta/duplicate behaviour on a throwaway copy.
        delta_correct, delta_total, dup_correct, dup_total = _delta_checks(ctx.workdir)

        # Performance + pipeline seeding: scan the shared workspace once.
        n_files = sum(1 for p in ctx.docs_dir.iterdir() if p.is_file())
        with Timer() as t:
            stats = scan(ctx.sources_yml, ctx.db_path, ctx.cache_dir)

        result.quality = {
            "delta_accuracy": fraction(delta_correct, delta_total),
            "duplicate_accuracy": fraction(dup_correct, dup_total),
            "files_indexed": stats.files_scanned,
        }
        result.performance = performance(n_files, t.seconds)
    except Exception as exc:  # noqa: BLE001 - surface as a stage error
        result.error = f"{type(exc).__name__}: {exc}"
    return result


__all__ = ["run"]
