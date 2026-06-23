import json

from catalog.cli import main
from catalog.semantic.providers.base import BaseLLMProvider

GOVERNANCE = {
    "document_type": "Governance",
    "type_confidence": 0.93,
    "short_summary": "Release governance summary.",
    "long_summary": "Longer release governance summary.",
    "domains": [{"domain": "Test & Release", "confidence": 0.9}],
    "capabilities": [{"name": "Release Management", "confidence": 0.92}],
    "entities": [{"entity_type": "Technology", "name": "SAP", "confidence": 0.8}],
    "decisions": [{"decision_text": "Use Launchpad model", "confidence": 0.84,
                   "supporting_text": "we will use launchpad"}],
    "risks": [{"risk_description": "Unclear ownership", "confidence": 0.7}],
    "relationships": [{"subject": "Release Governance", "predicate": "supports",
                       "object": "Launchpad Model", "confidence": 0.87}],
}


class StubProvider(BaseLLMProvider):
    def generate(self, prompt, *, system=None):
        return json.dumps(GOVERNANCE)


def _base_args(tmp_path):
    return ["--db", str(tmp_path / "catalog.sqlite"), "--cache", str(tmp_path / "cache")]


def _write_cache(cache_dir, artifact_id, text, filename):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": filename}), encoding="utf-8"
    )


def _classify(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "catalog.commands.semantic.build_provider", lambda cfg: StubProvider("stub-model")
    )
    _write_cache(tmp_path / "cache", "doc_gov", "release governance", "gov.pptx")
    assert main(_base_args(tmp_path) + ["classify"]) == 0


def test_classify_command(tmp_path, monkeypatch, capsys):
    _classify(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "Classification progress: 100% complete (1/1) doc_gov" in out
    assert "Documents processed: 1" in out
    assert "Governance" in out


def test_classification_stats_command(tmp_path, monkeypatch, capsys):
    _classify(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(_base_args(tmp_path) + ["classification-stats"]) == 0
    out = capsys.readouterr().out
    assert "Classified documents: 1" in out
    assert "Governance" in out
    assert "Release Management" in out
    assert "SAP" in out


def test_show_summary_command(tmp_path, monkeypatch, capsys):
    _classify(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(_base_args(tmp_path) + ["show-summary", "--artifact-id", "doc_gov"]) == 0
    out = capsys.readouterr().out
    assert "Type: Governance" in out
    assert "Release governance summary." in out
    assert "Test & Release" in out


def test_show_decisions_risks_caps_rels(tmp_path, monkeypatch, capsys):
    _classify(tmp_path, monkeypatch)
    capsys.readouterr()

    assert main(_base_args(tmp_path) + ["show-decisions"]) == 0
    assert "Use Launchpad model" in capsys.readouterr().out

    assert main(_base_args(tmp_path) + ["show-risks"]) == 0
    assert "Unclear ownership" in capsys.readouterr().out

    assert main(_base_args(tmp_path) + ["show-capabilities"]) == 0
    assert "Release Management" in capsys.readouterr().out

    assert main(_base_args(tmp_path) + ["show-relationships"]) == 0
    assert "supports" in capsys.readouterr().out


def test_classification_stats_empty(tmp_path, capsys):
    assert main(_base_args(tmp_path) + ["classification-stats"]) == 0
    assert "No classifications yet" in capsys.readouterr().out
