import io
import json
from contextlib import contextmanager

import pytest

from catalog.semantic.config import LLMConfig, load_llm_config
from catalog.semantic.providers import (
    OllamaProvider,
    OpenAIProvider,
    available_providers,
    build_provider,
)
from catalog.semantic.providers.base import BaseLLMProvider, LLMError


class StubProvider(BaseLLMProvider):
    def generate(self, prompt, *, system=None):
        return "{}"


def test_base_provider_requires_model():
    with pytest.raises(ValueError):
        StubProvider("")


def test_base_provider_exposes_model():
    assert StubProvider("my-model").model == "my-model"


def test_build_provider_ollama_from_config():
    cfg = LLMConfig(provider="ollama", model="qwen3:14b", options={"host": "http://h:1"})
    provider = build_provider(cfg)
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3:14b"
    assert provider.host == "http://h:1"


def test_build_provider_openai_from_config():
    cfg = LLMConfig(provider="openai", model="gpt-5.5", options={"base_url": "http://api/v9"})
    provider = build_provider(cfg)
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-5.5"
    assert provider.base_url == "http://api/v9"


def test_build_provider_unknown_raises():
    with pytest.raises(LLMError):
        build_provider(LLMConfig(provider="does-not-exist", model="x"))


def test_available_providers_lists_both():
    assert set(available_providers()) >= {"ollama", "openai"}


def test_load_llm_config_defaults_when_missing(tmp_path):
    cfg = load_llm_config(tmp_path / "absent.yml")
    assert cfg.provider == "ollama"
    assert cfg.model == "qwen3:14b"


def test_load_llm_config_reads_selected_provider(tmp_path):
    path = tmp_path / "llm.yml"
    path.write_text(
        "provider: openai\n"
        "openai:\n  model: gpt-5.5\n  base_url: http://x\n"
        "ollama:\n  model: qwen3:14b\n"
        "max_input_chars: 500\n",
        encoding="utf-8",
    )
    cfg = load_llm_config(path)
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.5"
    assert cfg.max_input_chars == 500
    assert cfg.options["base_url"] == "http://x"


@contextmanager
def _fake_urlopen(captured, response_bytes):
    def _open(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["headers"] = req.headers
        return io.BytesIO(response_bytes)

    yield _open


def test_ollama_generate_posts_and_parses(monkeypatch):
    captured = {}
    body = json.dumps({"response": '{"document_type": "Report"}'}).encode("utf-8")
    with _fake_urlopen(captured, body) as opener:
        monkeypatch.setattr("catalog.semantic.providers.ollama_provider.request.urlopen", opener)
        provider = OllamaProvider("qwen3:14b", host="http://localhost:11434")
        out = provider.generate("hello", system="sys")
    assert out == '{"document_type": "Report"}'
    assert captured["url"].endswith("/api/generate")
    assert captured["body"]["model"] == "qwen3:14b"
    assert captured["body"]["system"] == "sys"
    assert captured["body"]["stream"] is False


def test_openai_generate_posts_and_parses(monkeypatch):
    captured = {}
    body = json.dumps(
        {"choices": [{"message": {"content": '{"document_type": "Strategy"}'}}]}
    ).encode("utf-8")
    with _fake_urlopen(captured, body) as opener:
        monkeypatch.setattr("catalog.semantic.providers.openai_provider.request.urlopen", opener)
        provider = OpenAIProvider("gpt-5.5", api_key="secret")
        out = provider.generate("hello", system="sys")
    assert out == '{"document_type": "Strategy"}'
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["messages"][0]["role"] == "system"


def test_openai_without_key_raises():
    provider = OpenAIProvider("gpt-5.5", api_key="")
    with pytest.raises(LLMError):
        provider.generate("hi")
