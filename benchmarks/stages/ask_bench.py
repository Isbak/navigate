"""Ask stage benchmark (GraphRAG assistant).

Quality measures graph-first retrieval and the hallucination controls:

* ``retrieval_recall`` - for answerable questions, the fraction of expected
  knowledge objects that the retriever surfaced;
* ``groundedness_accuracy`` - whether ``answer.supported`` matches expectation,
  including that an unanswerable question is correctly declined (no objects ->
  no support);
* ``citation_rate`` - supported answers must carry at least one cited object.

Performance is answered questions per second (the graph projection is built once
and reused, matching production behaviour).
"""

from __future__ import annotations

from catalog.db import connect
from catalog.graph.client import GraphClient
from catalog.graphrag.assistant import GraphRAGAssistant

from .. import corpus as corpus_mod
from ..metrics import StageResult, Timer, fraction, mean, performance


def _build_assistant(ctx) -> GraphRAGAssistant:
    with connect(ctx.db_path) as conn:
        client = GraphClient.from_sqlite(conn)
    return GraphRAGAssistant(client, ctx.answer_provider)


def run(ctx) -> StageResult:
    result = StageResult(stage="ask")
    try:
        # The retriever only projects APPROVED rows, so approve the graph first.
        corpus_mod.approve_all(ctx.db_path)
        assistant = _build_assistant(ctx)

        questions = ctx.corpus.ask["questions"]
        recalls: list[float] = []
        grounded_correct = 0
        supported_total = 0
        cited_total = 0

        with Timer() as t:
            answers = [assistant.ask(q["question"]) for q in questions]

        for q, answer in zip(questions, answers, strict=False):
            if answer.supported == q["expect_supported"]:
                grounded_correct += 1

            expected = set(q.get("expected_object_ids", []))
            if expected:
                retrieved = {obj.id for obj in answer.retrieval.objects}
                recalls.append(len(expected & retrieved) / len(expected))

            if answer.supported:
                supported_total += 1
                if answer.citations.objects:
                    cited_total += 1

        result.quality = {
            "retrieval_recall": mean(recalls),
            "groundedness_accuracy": fraction(grounded_correct, len(questions)),
            "citation_rate": fraction(cited_total, supported_total),
            "questions": len(questions),
        }
        result.performance = performance(len(questions), t.seconds)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    return result


__all__ = ["run"]
