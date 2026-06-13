"""Semantic classification and knowledge discovery (Prompt #5).

This package uses an LLM to analyze extracted documents and *propose* structured
knowledge: a document classification, the domains and capabilities it discusses,
candidate entities, and candidate decisions, risks, and relationships. It reads
the extraction cache and SQLite catalog and writes new SQLite semantic tables.

Design principles
-----------------
* Documents are evidence; knowledge emerges from documents.
* The LLM proposes, humans approve. Nothing here is stored as a FACT.
* Every semantic object carries provenance (artifact_id, model, timestamp), a
  confidence in ``[0.0, 1.0]``, the supporting text it came from, a
  knowledge_type (OBSERVATION or HYPOTHESIS), and a review_status that starts at
  NEW.
* No RDF, no Jena, no GraphRAG - those are explicitly future modules.

The provider abstraction (``providers``) keeps the service agnostic to whether
completions come from Ollama or OpenAI, so new backends are easy to add.
"""

from __future__ import annotations

from .analytics import (
    concepts_connecting_domains,
    decision_themes,
    document_types,
    risk_themes,
    top_capabilities,
    top_concepts,
    top_domains,
    top_technologies,
)
from .config import LLMConfig, load_llm_config
from .models import (
    ClassificationResult,
    KnowledgeType,
    ReviewStatus,
)
from .parser import parse_classification_response, validate_confidence
from .prompts import build_classification_prompt
from .providers import BaseLLMProvider, LLMError, build_provider
from .service import ClassifyStats, classify_documents

__all__ = [
    "LLMConfig",
    "load_llm_config",
    "BaseLLMProvider",
    "LLMError",
    "build_provider",
    "ClassificationResult",
    "KnowledgeType",
    "ReviewStatus",
    "build_classification_prompt",
    "parse_classification_response",
    "validate_confidence",
    "ClassifyStats",
    "classify_documents",
    "document_types",
    "top_domains",
    "top_capabilities",
    "top_technologies",
    "top_concepts",
    "decision_themes",
    "risk_themes",
    "concepts_connecting_domains",
]
