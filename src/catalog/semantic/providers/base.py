"""Provider-agnostic LLM interface.

The semantic layer never talks to a vendor SDK directly; it talks to a
:class:`BaseLLMProvider`. A provider takes a prompt and returns raw text. All
JSON parsing, confidence validation, and persistence happen above this layer,
so swapping Ollama for OpenAI (or adding a new backend) changes nothing in the
classification service.

To add a provider:

* subclass :class:`BaseLLMProvider`,
* implement :meth:`generate`,
* register it in :func:`catalog.semantic.providers.build_provider`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a completion."""


class BaseLLMProvider(ABC):
    """Abstract base for all LLM backends.

    Subclasses expose the concrete model name via :attr:`model` so it can be
    recorded as provenance on every semantic object, and implement
    :meth:`generate` to turn a prompt into a single text completion.
    """

    def __init__(self, model: str) -> None:
        if not model:
            raise ValueError("A model name is required")
        self._model = model

    @property
    def model(self) -> str:
        """The concrete model identifier, recorded as provenance."""

        return self._model

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's text completion for ``prompt``.

        ``system`` is an optional system / instruction message. Implementations
        should raise :class:`LLMError` on transport or API failures so the
        classification service can count the artifact as an error and move on.
        """

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}(model={self._model!r})"


__all__ = ["BaseLLMProvider", "LLMError"]
