"""The benchmark gold corpus: fixture documents plus expected outputs.

Everything the harness needs to drive the pipeline deterministically lives under
``benchmarks/corpus/``:

    corpus/docs/        small in-repo fixture documents (.md / .txt)
    corpus/gold/        per-stage expected outputs (extract/classify/consolidate/ask)

This module loads those assets and provides small helpers to materialize a
working copy of the documents (so mutation-based scan tests never touch the
originals) and to approve a consolidated graph (so the ask stage has an approved
graph to retrieve over, mirroring ``tests/conftest.py``).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from catalog.db import connect
from catalog.knowledge.models import ReviewState
from catalog.knowledge.service import review_object, review_relationship

CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"
DOCS_DIR = CORPUS_ROOT / "docs"
GOLD_DIR = CORPUS_ROOT / "gold"


@dataclass
class Corpus:
    """In-memory view of the gold corpus."""

    docs_dir: Path
    extract: dict
    classify: dict
    consolidate: dict
    ask: dict

    @property
    def filenames(self) -> list[str]:
        return sorted(self.classify.keys())

    def classify_responses(self) -> dict[str, dict]:
        """The canned LLM responses keyed by filename for the stub provider."""

        return self.classify


def _load_json(name: str) -> dict:
    return json.loads((GOLD_DIR / name).read_text(encoding="utf-8"))


def load_corpus() -> Corpus:
    """Load the gold corpus from disk."""

    return Corpus(
        docs_dir=DOCS_DIR,
        extract=_load_json("extract.json"),
        classify=_load_json("classify.json"),
        consolidate=_load_json("consolidate.json"),
        ask=_load_json("ask.json"),
    )


def materialize_docs(dest: Path, *, source: Path = DOCS_DIR) -> Path:
    """Copy the fixture documents into ``dest`` and return the directory.

    Used so a stage that mutates files (the scan delta test) operates on a
    throwaway copy, leaving the in-repo corpus pristine.
    """

    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.iterdir()):
        if path.is_file():
            shutil.copy2(path, dest / path.name)
    return dest


def write_sources_yml(path: Path, docs_dir: Path, *, source_system: str = "benchmark") -> Path:
    """Write a minimal ``sources.yml`` pointing the scanner at ``docs_dir``."""

    path.write_text(
        yaml.safe_dump(
            {
                "sources": [{"path": str(docs_dir), "source_system": source_system}],
                "exclude": ["**/.git/**"],
            }
        ),
        encoding="utf-8",
    )
    return path


def approve_all(db_path: str | Path) -> None:
    """Approve every knowledge object and relationship (mirrors conftest).

    The GraphRAG retriever only ever projects ``APPROVED`` rows, so the ask stage
    needs an approved graph to retrieve over.
    """

    with connect(db_path) as conn:
        object_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
        rel_ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_relationships")]
    for object_id in object_ids:
        review_object(db_path, object_id, ReviewState.APPROVED.value)
    for rel_id in rel_ids:
        review_relationship(db_path, rel_id, ReviewState.APPROVED.value)


__all__ = [
    "Corpus",
    "CORPUS_ROOT",
    "DOCS_DIR",
    "GOLD_DIR",
    "load_corpus",
    "materialize_docs",
    "write_sources_yml",
    "approve_all",
]
