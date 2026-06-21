"""Code-aware chunking.

The document chunker (:func:`catalog.semantic.prompts.chunk_text`) splits on
fixed character offsets, which would slice a function in half. For source code
we instead break only at top-level construct boundaries (functions, classes,
import blocks) so every definition reaches the model whole. The whole file is
preserved verbatim - chunks are contiguous spans of the original lines, so no
bytes are dropped or reordered.

When a single top-level construct is larger than ``max_chars`` (a very long
function), that span alone is character-split as a last resort. When no grammar
is available we fall back entirely to :func:`chunk_text`.
"""

from __future__ import annotations

from .parser import get_parser


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    # Imported lazily: the semantic package imports this `code` package, so a
    # module-level import here would form a cycle at startup.
    from ..semantic.prompts import chunk_text

    return chunk_text(text, size, overlap)


def _boundary_lines(code: str, language: str) -> list[int] | None:
    """0-based line numbers where a new top-level construct begins.

    Returns ``None`` when the file cannot be parsed, signalling the caller to
    use the plain-text chunker.
    """

    parser = get_parser(language)
    if parser is None:
        return None
    try:
        tree = parser.parse(code.encode("utf-8"))
        root = tree.root_node
    except Exception:  # noqa: BLE001 - fall back to char chunking on parse error
        return None
    return sorted({child.start_point[0] for child in root.children})


def chunk_code(
    code: str, language: str | None, max_chars: int, overlap: int = 0
) -> list[str]:
    """Split ``code`` into <= ``max_chars`` chunks aligned to code boundaries.

    Each chunk is a run of whole top-level constructs. ``overlap`` is honoured
    only when an oversized single construct must be character-split (so its
    fallback matches :func:`chunk_text`); distinct constructs are never
    overlapped because they are already self-contained.
    """

    body = code or ""
    if max_chars <= 0 or len(body) <= max_chars:
        return [body]

    boundaries = _boundary_lines(body, language) if language else None
    if not boundaries:
        return _chunk_text(body, max_chars, overlap)

    lines = body.splitlines(keepends=True)
    # Segment the file at construct boundaries; each segment is a (whole) span of
    # one or more lines that we may start a new chunk on.
    starts = sorted(set(boundaries) | {0, len(lines)})
    segments = [
        "".join(lines[starts[i] : starts[i + 1]]) for i in range(len(starts) - 1)
    ]

    chunks: list[str] = []
    current = ""
    for segment in segments:
        if len(segment) > max_chars:
            # A single construct exceeds the budget: flush, then char-split it.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_chunk_text(segment, max_chars, overlap))
            continue
        if current and len(current) + len(segment) > max_chars:
            chunks.append(current)
            current = segment
        else:
            current += segment
    if current:
        chunks.append(current)
    return chunks or [body]


def select_chunks(
    text: str, language: str | None, max_chars: int, overlap: int = 0
) -> list[str]:
    """Code-aware chunking for source files, char chunking for everything else."""

    if language:
        return chunk_code(text, language, max_chars, overlap)
    return _chunk_text(text, max_chars, overlap)


__all__ = ["chunk_code", "select_chunks"]
