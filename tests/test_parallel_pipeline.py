"""Parallel pipeline stages must match the serial path exactly.

``extract``, ``discover-links`` and ``classify`` gained an opt-in worker pool.
The work items are independent and all DB writes stay on one thread, so running
with more workers must produce identical cache files, identical persisted rows,
and identical summary counters - only faster. These tests lock that in.
"""

import json

from catalog.config import resolve_workers
from catalog.db import connect
from catalog.extraction import extract_all
from catalog.links import discover_links
from catalog.links import repository as link_repo
from catalog.links.config import LinkConfig
from catalog.scanner import scan
from catalog.semantic import repository as sem_repo
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.routing import single_provider_router
from catalog.semantic.service import classify_documents

# -- helpers ------------------------------------------------------------------

def _write_sources(tmp_path, root):
    config = tmp_path / "sources.yml"
    config.write_text(
        f"sources:\n  - path: '{root}'\n    source_system: 'test'\n"
        "index_code: false\nexclude: []\n",
        encoding="utf-8",
    )
    return config


def _write_link_cache(cache_dir, artifact_id, raw_links):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "links.json").write_text(json.dumps(raw_links), encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id}), encoding="utf-8"
    )


def _write_doc_cache(cache_dir, artifact_id, text, filename):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": filename}),
        encoding="utf-8",
    )


class _StubProvider(BaseLLMProvider):
    """Deterministic per-filename responses (no usage reported)."""

    def __init__(self, responses):
        super().__init__("stub-model")
        self.responses = responses

    def generate(self, prompt, *, system=None):
        for key, payload in self.responses.items():
            if key in prompt:
                return json.dumps(payload)
        return json.dumps({"document_type": "Report", "type_confidence": 0.4})


# -- resolve_workers ----------------------------------------------------------

def test_resolve_workers_precedence():
    # Flag wins over config.
    assert resolve_workers(3, 8) == 3
    # No flag -> config value.
    assert resolve_workers(None, 5) == 5
    # 0 / None / negative -> auto (CPU count), always >= 1.
    assert resolve_workers(0, 0) >= 1
    assert resolve_workers(None, 0) >= 1
    assert resolve_workers(-1, 4) >= 1


# -- extract ------------------------------------------------------------------

def test_extract_parallel_matches_serial(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(5):
        (repo / f"doc{i}.md").write_text(
            f"# Doc {i}\nSee https://example.com/{i} and file://local/{i}.txt\n",
            encoding="utf-8",
        )
    config = _write_sources(tmp_path, repo)

    def _run(cache_name, workers):
        db = tmp_path / f"{cache_name}.sqlite"
        cache = tmp_path / cache_name
        scan(config, db, cache)
        summary = extract_all(db, cache, workers=workers)
        files = {}
        for d in sorted(cache.glob("*/")):
            extracted = d / "extracted.txt"
            links = d / "links.json"
            if extracted.exists():
                files[d.name] = (
                    extracted.read_text(encoding="utf-8"),
                    links.read_text(encoding="utf-8") if links.exists() else None,
                )
        return summary, files

    serial_summary, serial_files = _run("serial", workers=1)
    parallel_summary, parallel_files = _run("parallel", workers=4)

    assert serial_summary == parallel_summary
    assert serial_summary["artifacts_processed"] == 5
    assert serial_files == parallel_files


# -- discover-links -----------------------------------------------------------

def test_discover_links_parallel_matches_serial(tmp_path):
    raw = {
        "doc_a": [{"raw_url": "https://github.com/acme/repo/pull/3", "anchor_text": "PR"}],
        "doc_b": [{"raw_url": "https://example.com/p?utm_source=x&id=1", "anchor_text": None}],
        "doc_c": [
            {"raw_url": "https://example.com/a", "anchor_text": "A"},
            {"raw_url": "mailto:team@example.com", "anchor_text": "mail"},
        ],
        "doc_d": [{"raw_url": "https://github.com/acme/repo/issues/9", "anchor_text": "I"}],
    }

    def _run(name, workers):
        db = tmp_path / f"{name}.sqlite"
        cache = tmp_path / name
        for aid, links in raw.items():
            _write_link_cache(cache, aid, links)
        stats = discover_links(db, cache, LinkConfig.empty(), workers=workers)
        with connect(db) as conn:
            rows = [
                (r["source_artifact_id"], r["normalized_url"], r["target_system"],
                 r["target_type"], r["link_kind"])
                for r in link_repo.all_links(conn)
            ]
        return stats.as_dict(), sorted(rows)

    serial_stats, serial_rows = _run("serial", workers=1)
    parallel_stats, parallel_rows = _run("parallel", workers=4)

    assert serial_stats == parallel_stats
    assert serial_stats["artifacts_processed"] == 4
    assert serial_stats["links_found"] == 5
    assert serial_rows == parallel_rows


# -- classify -----------------------------------------------------------------

def test_classify_parallel_matches_serial(tmp_path):
    responses = {
        f"doc{i}.txt": {
            "document_type": "Governance" if i % 2 else "Report",
            "type_confidence": 0.9,
            "short_summary": f"summary {i}",
            "entities": [{"entity_type": "Concept", "name": f"E{i}", "confidence": 0.8}],
        }
        for i in range(6)
    }

    def _run(name, workers):
        db = tmp_path / f"{name}.sqlite"
        cache = tmp_path / name
        for i in range(6):
            _write_doc_cache(cache, f"doc_{i}", f"body {i} " * 20, f"doc{i}.txt")
        stats = classify_documents(
            db, cache, _StubProvider(responses), workers=workers
        )
        with connect(db) as conn:
            rows = [
                (r["artifact_id"], r["document_type"], r["short_summary"])
                for r in sem_repo.all_classifications(conn)
            ]
        return stats.as_dict(), rows

    serial_stats, serial_rows = _run("serial", workers=1)
    parallel_stats, parallel_rows = _run("parallel", workers=4)

    assert serial_stats == parallel_stats
    assert serial_stats["documents_processed"] == 6
    assert serial_rows == parallel_rows


def test_classify_parallel_records_usage_like_serial(tmp_path):
    """With a usage-reporting provider + factory, parallel usage matches serial."""

    from catalog.cost.pricing import ModelRate, PricingTable
    from catalog.cost.usage import Usage

    class _UsageProvider(BaseLLMProvider):
        def __init__(self):
            super().__init__("m1")

        def generate(self, prompt, *, system=None):
            self._last_usage = Usage("m1", 100, 20)
            return json.dumps({"document_type": "Report", "type_confidence": 0.5})

    pricing = PricingTable(rates={"m1": ModelRate(3.0, 15.0)})

    def _run(name, workers):
        db = tmp_path / f"{name}.sqlite"
        cache = tmp_path / name
        for i in range(4):
            _write_doc_cache(cache, f"doc_{i}", f"body {i} " * 10, f"doc{i}.txt")
        # A factory gives each worker its own provider, so last_usage is never
        # shared across threads and usage is attributed correctly.
        classify_documents(
            db, cache, _UsageProvider(),
            router_factory=lambda: single_provider_router(
                _UsageProvider(), max_chunks=20
            ),
            pricing=pricing, provider_name="stub", workers=workers,
        )
        with connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE operation='classify'"
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage"
            ).fetchone()[0]
        return count, round(total, 6)

    assert _run("serial", workers=1) == _run("parallel", workers=4)
