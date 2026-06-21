import io
import json
from contextlib import contextmanager

import pytest

from catalog.cli import main
from catalog.cost import (
    NullUsageLedger,
    PricingTable,
    UsageLedger,
    compute_cost,
    load_pricing,
)
from catalog.cost import repository as cost_repo
from catalog.cost.pricing import ModelRate
from catalog.cost.usage import Usage
from catalog.db import connect, init_db
from catalog.semantic.providers import ClaudeProvider, OllamaProvider, OpenAIProvider
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.service import classify_documents


# -- provider usage capture ---------------------------------------------------

@contextmanager
def _fake_urlopen(response_bytes):
    def _open(req, timeout=None):
        return io.BytesIO(response_bytes)

    yield _open


def test_claude_captures_usage(monkeypatch):
    body = json.dumps(
        {
            "content": [{"type": "text", "text": "{}"}],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 34,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 7,
            },
        }
    ).encode("utf-8")
    with _fake_urlopen(body) as opener:
        monkeypatch.setattr(
            "catalog.semantic.providers.claude_provider.request.urlopen", opener
        )
        provider = ClaudeProvider("claude-sonnet-4-5", api_key="secret")
        provider.generate("hi")
    usage = provider.last_usage
    assert usage is not None
    assert usage.model == "claude-sonnet-4-5"
    assert usage.input_tokens == 12
    assert usage.output_tokens == 34
    assert usage.cache_read_tokens == 5
    assert usage.cache_write_tokens == 7
    assert usage.total_tokens == 46
    assert usage.latency_ms is not None


def test_openai_captures_usage(monkeypatch):
    body = json.dumps(
        {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
    ).encode("utf-8")
    with _fake_urlopen(body) as opener:
        monkeypatch.setattr(
            "catalog.semantic.providers.openai_provider.request.urlopen", opener
        )
        provider = OpenAIProvider("gpt-5.5", api_key="secret")
        provider.generate("hi")
    assert provider.last_usage == Usage(
        "gpt-5.5", 100, 20, latency_ms=provider.last_usage.latency_ms
    )


def test_ollama_captures_usage(monkeypatch):
    body = json.dumps(
        {"response": "{}", "prompt_eval_count": 8, "eval_count": 3}
    ).encode("utf-8")
    with _fake_urlopen(body) as opener:
        monkeypatch.setattr(
            "catalog.semantic.providers.ollama_provider.request.urlopen", opener
        )
        provider = OllamaProvider("qwen3:14b")
        provider.generate("hi")
    assert provider.last_usage.input_tokens == 8
    assert provider.last_usage.output_tokens == 3


def test_failed_call_resets_usage(monkeypatch):
    # First call succeeds and sets usage; second raises and must clear it.
    ok = json.dumps(
        {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 1, "output_tokens": 1}}
    ).encode("utf-8")
    with _fake_urlopen(ok) as opener:
        monkeypatch.setattr(
            "catalog.semantic.providers.claude_provider.request.urlopen", opener
        )
        provider = ClaudeProvider("claude-sonnet-4-5", api_key="secret")
        provider.generate("hi")
    assert provider.last_usage is not None

    def _boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(
        "catalog.semantic.providers.claude_provider.request.urlopen", _boom
    )
    with pytest.raises(Exception):
        provider.generate("hi again")
    assert provider.last_usage is None


def test_stub_provider_reports_no_usage():
    class StubProvider(BaseLLMProvider):
        def generate(self, prompt, *, system=None):
            return "{}"

    provider = StubProvider("stub")
    assert provider.last_usage is None
    provider.generate("hi")
    assert provider.last_usage is None


# -- pricing ------------------------------------------------------------------

def test_load_pricing_missing_file_is_empty(tmp_path):
    table = load_pricing(tmp_path / "absent.yml")
    assert table.rates == {}


def test_load_pricing_reads_rates(tmp_path):
    path = tmp_path / "pricing.yml"
    path.write_text(
        "currency: USD\nmodels:\n"
        "  m1:\n    input: 3.0\n    output: 15.0\n",
        encoding="utf-8",
    )
    table = load_pricing(path)
    assert table.rate_for("m1") == ModelRate(3.0, 15.0)
    assert table.rate_for("unknown") is None


def test_compute_cost_known_and_unknown():
    table = PricingTable(rates={"m1": ModelRate(3.0, 15.0)})
    # 1M input @ $3 + 1M output @ $15 = $18
    cost = compute_cost(Usage("m1", 1_000_000, 1_000_000), table)
    assert cost == pytest.approx(18.0)
    assert compute_cost(Usage("nope", 100, 100), table) is None


def test_compute_cost_includes_cache_tokens():
    table = PricingTable(
        rates={"m1": ModelRate(3.0, 15.0, cache_read_per_1m=0.3, cache_write_per_1m=3.75)}
    )
    usage = Usage("m1", 0, 0, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert compute_cost(usage, table) == pytest.approx(0.3 + 3.75)


# -- repository ---------------------------------------------------------------

def _seed(conn, **kwargs):
    defaults = dict(
        operation="classify",
        model="m1",
        provider="claude",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return cost_repo.record_usage(conn, **defaults)


def test_repository_totals_and_groupings(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed(conn, operation="classify", artifact_id="doc_a", cost_usd=0.01)
        _seed(conn, operation="classify", artifact_id="doc_a", cost_usd=0.02)
        _seed(conn, operation="vision-extract", artifact_id="doc_a", cost_usd=0.03)
        # An unpriced model: tokens tracked, cost NULL.
        _seed(conn, operation="ask", model="local", artifact_id=None, cost_usd=None)
        conn.commit()

        totals = cost_repo.totals(conn)
        assert totals["calls"] == 4
        assert totals["total_tokens"] == 4 * 15
        assert totals["cost_usd"] == pytest.approx(0.06)
        assert totals["unpriced_calls"] == 1

        ops = {r["key"]: r for r in cost_repo.by_operation(conn)}
        assert ops["classify"]["calls"] == 2
        assert ops["vision-extract"]["cost_usd"] == pytest.approx(0.03)

        models = {r["key"]: r for r in cost_repo.by_model(conn)}
        assert models["local"]["unpriced_calls"] == 1
        assert models["local"]["cost_usd"] is None

        per_doc = cost_repo.cost_per_document(conn)
        # doc_a aggregates classify + vision; the unpriced ask has no artifact_id.
        assert len(per_doc) == 1
        assert per_doc[0]["key"] == "doc_a"
        assert per_doc[0]["calls"] == 3
        assert per_doc[0]["cost_usd"] == pytest.approx(0.06)


def test_cost_vs_quality_joins_confidence(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO document_classifications("
            "artifact_id, document_type, type_confidence, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("doc_a", "Governance", 0.93, "2026-01-01T00:00:00+00:00"),
        )
        _seed(conn, artifact_id="doc_a", cost_usd=0.05)
        conn.commit()
        rows = cost_repo.cost_vs_quality(conn)
    assert rows[0]["key"] == "doc_a"
    assert rows[0]["document_type"] == "Governance"
    assert rows[0]["type_confidence"] == pytest.approx(0.93)


# -- ledger -------------------------------------------------------------------

class _UsageProvider(BaseLLMProvider):
    """Stub provider that reports a fixed usage on every call."""

    def __init__(self, model="m1", usage=None):
        super().__init__(model)
        self._fixed = usage or Usage(model, 10, 5)

    def generate(self, prompt, *, system=None):
        self._last_usage = self._fixed
        return "{}"


def test_usage_ledger_records_priced_row(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    pricing = PricingTable(rates={"m1": ModelRate(3.0, 15.0)})
    provider = _UsageProvider("m1", Usage("m1", 1_000_000, 0))
    provider.generate("hi")
    with connect(db) as conn:
        ledger = UsageLedger(conn, pricing, provider_name="stub")
        ledger.record(provider, operation="classify", artifact_id="doc_x")
        conn.commit()
        row = conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["operation"] == "classify"
    assert row["artifact_id"] == "doc_x"
    assert row["provider"] == "stub"
    assert row["cost_usd"] == pytest.approx(3.0)


def test_null_ledger_records_nothing(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    provider = _UsageProvider()
    provider.generate("hi")
    ledger = NullUsageLedger()
    ledger.record(provider, operation="classify", artifact_id="doc_x")
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


# -- service wiring -----------------------------------------------------------

def _write_cache(cache_dir, artifact_id, text, filename):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": filename}),
        encoding="utf-8",
    )


def test_classify_records_usage_per_chunk(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    # Long text + small chunk size forces multiple chunks (one call each).
    _write_cache(cache, "doc_gov", "release governance " * 400, "gov.pptx")

    provider = _UsageProvider("m1", Usage("m1", 100, 20))
    pricing = PricingTable(rates={"m1": ModelRate(3.0, 15.0)})
    classify_documents(
        db, cache, provider, max_input_chars=500, chunk_overlap=0,
        max_chunks=10, pricing=pricing, provider_name="stub",
    )

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM llm_usage WHERE operation='classify'"
        ).fetchall()
    assert len(rows) >= 2  # multiple chunks recorded
    assert all(r["artifact_id"] == "doc_gov" for r in rows)
    assert all(r["model"] == "m1" for r in rows)
    assert all(r["cost_usd"] is not None for r in rows)


def test_classify_with_stub_provider_records_nothing(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_a", "hello world", "a.txt")

    class StubProvider(BaseLLMProvider):
        def generate(self, prompt, *, system=None):
            return json.dumps({"document_type": "Report", "type_confidence": 0.5})

    classify_documents(db, cache, StubProvider("stub"))
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


# -- schema drift -------------------------------------------------------------

def test_init_db_rebuilds_stale_usage_table(tmp_path):
    db = tmp_path / "catalog.sqlite"
    with connect(db) as conn:
        # An older llm_usage layout missing the cache/latency columns.
        conn.execute(
            "CREATE TABLE llm_usage(id INTEGER PRIMARY KEY, operation TEXT, model TEXT)"
        )
        conn.execute("INSERT INTO llm_usage(operation, model) VALUES ('x', 'm')")
        conn.commit()

    init_db(db)  # should drop and recreate the table with the current columns

    with connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(llm_usage)")}
        assert "cache_read_tokens" in cols
        assert "latency_ms" in cols
        # Regenerable table: old rows are gone after the rebuild.
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


# -- CLI ----------------------------------------------------------------------

def test_cost_report_cli_table_and_json(tmp_path, capsys):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed(conn, operation="classify", artifact_id="doc_a", cost_usd=0.01)
        _seed(conn, operation="vision-extract", artifact_id="doc_a", cost_usd=0.02)
        conn.commit()

    base = ["--db", str(db), "--cache", str(tmp_path / "cache")]

    assert main(base + ["cost-report"]) == 0
    out = capsys.readouterr().out
    assert "LLM cost report" in out
    assert "Cost per document" in out
    assert "doc_a" in out

    out_path = tmp_path / "cost.json"
    assert main(base + ["cost-report", "--format", "json", "--out", str(out_path)]) == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["totals"]["calls"] == 2
    assert data["totals"]["cost_usd"] == pytest.approx(0.03)
    assert data["cost_per_document"][0]["key"] == "doc_a"
