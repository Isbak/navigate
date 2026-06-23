"""Knowledge review/approval commands: ``show-object``, ``search-knowledge``,
``review-candidates``, ``approve-object``, ``reject-object``,
``approve-relationship``, ``reject-relationship``,
``approve-confidence-interval``."""

from __future__ import annotations

import argparse

from ..db import connect, init_db
from ..governance import service as gov_service
from ..knowledge import analytics as know_analytics
from ..knowledge import repository as know_repo
from ..knowledge.models import ReviewState
from ..knowledge.service import (
    approve_relationships_by_confidence,
    review_object,
    review_relationship,
)
from ._common import fmt


def _cmd_show_object(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        obj = know_repo.get_object(conn, args.object_id)
        if obj is None:
            print(f"No knowledge object with id {args.object_id!r}.")
            return
        mentions = know_repo.mentions_for_object(conn, args.object_id)
        evidence = know_repo.evidence_for_object(conn, args.object_id)
        rels = know_repo.relationships_for_object(conn, args.object_id)

    documents = len({m["artifact_id"] for m in mentions})
    print(f"{obj['canonical_name']}")
    print(f"\nId: {obj['id']}")
    print(f"Type: {obj['object_type']}")
    print(f"Confidence: {obj['confidence']:.2f}")
    print(f"Status: {obj['status']}    Merge confidence: {obj['merge_confidence']:.2f}")
    print(f"Mentions: {fmt(len(mentions))}")
    print(f"Documents: {fmt(documents)}")
    if obj["description"]:
        print(f"\nDescription:\n{obj['description']}")

    print("\nRelated objects:")
    if not rels:
        print("  (none)")
    for r in rels:
        if r["source_object"] == args.object_id:
            print(
                f"  {r['predicate']} -> {r['target_object']} "
                f"[{r['confidence']:.2f}] ({r['review_status']}) id={r['id']}"
            )
        else:
            print(
                f"  {r['source_object']} -> {r['predicate']} (this) "
                f"[{r['confidence']:.2f}] ({r['review_status']}) id={r['id']}"
            )

    print("\nEvidence:")
    if not evidence:
        print("  (none)")
    for e in evidence[:15]:
        locator = ""
        if e["slide_number"] is not None:
            locator = f" slide {e['slide_number']}"
        elif e["page_number"] is not None:
            locator = f" page {e['page_number']}"
        quote = e["quote"] or ""
        print(f'  {e["artifact_id"]}{locator}: "{quote}"')


def _cmd_search_knowledge(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        rows = know_repo.search_objects(conn, args.query)
    if not rows:
        print(f"No knowledge objects match {args.query!r}.")
        return
    for r in rows:
        print(
            f"[{r['confidence']:.2f}] {r['canonical_name']} "
            f"({r['object_type']}, {r['status']})  id={r['id']}"
        )


def _cmd_review_candidates(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        objects = know_repo.objects_by_status(conn, ReviewState.PROPOSED.value)
        rels = know_repo.relationships_by_status(conn, ReviewState.PROPOSED.value)
        dups = know_analytics.duplicate_candidates(conn, limit=20)
        cross_type = know_analytics.cross_type_duplicates(conn, limit=20)

    print(f"Proposed objects awaiting review: {fmt(len(objects))}")
    for r in objects[:30]:
        print(f"  [{r['confidence']:.2f}] {r['canonical_name']} ({r['object_type']})  id={r['id']}")

    print(f"\nProposed relationships awaiting review: {fmt(len(rels))}")
    for r in rels[:30]:
        print(
            f"  [{r['confidence']:.2f}] {r['source_object']} {r['predicate']} {r['target_object']}  id={r['id']}"
        )

    print(f"\nDuplicate candidates (possible merges): {fmt(len(dups))}")
    for d in dups:
        print(
            f"  [{d['similarity']:.2f}] {d['left_name']} <-> {d['right_name']} ({d['object_type']})"
        )

    print(f"\nSame name, different type (possible mis-typing): {fmt(len(cross_type))}")
    for d in cross_type:
        print(f"  {d['left_name']} ({d['left_type']}) <-> {d['right_name']} ({d['right_type']})")


def _cmd_review_object(args: argparse.Namespace, status: str) -> None:
    init_db(args.db)
    changed = review_object(args.db, args.object_id, status)
    if changed:
        print(f"{args.object_id} -> {status}")
    else:
        print(f"No knowledge object with id {args.object_id!r}.")


def _cmd_review_relationship(args: argparse.Namespace, status: str) -> None:
    init_db(args.db)
    changed = review_relationship(args.db, args.relationship_id, status)
    if changed:
        print(f"relationship {args.relationship_id} -> {status}")
    else:
        print(f"No knowledge relationship with id {args.relationship_id!r}.")


def _cmd_approve_confidence_interval(args: argparse.Namespace) -> None:
    init_db(args.db)
    object_statuses = [ReviewState.PROPOSED.value]
    relationship_statuses = [ReviewState.PROPOSED.value]
    if args.include_reviewed:
        object_statuses.append(ReviewState.REVIEWED.value)
        relationship_statuses.append(ReviewState.REVIEWED.value)

    objects_approved = 0
    relationships_approved = 0
    try:
        if args.target in {"objects", "all"}:
            for status in object_statuses:
                obj_stats = gov_service.approve_objects_by_confidence(
                    args.db,
                    args.min_confidence,
                    args.max_confidence,
                    reviewer="cli",
                    note=args.note,
                    current_status=status,
                )
                objects_approved += obj_stats.objects_approved
        if args.target in {"relationships", "all"}:
            for status in relationship_statuses:
                rel_stats = approve_relationships_by_confidence(
                    args.db,
                    args.min_confidence,
                    args.max_confidence,
                    reviewer="cli",
                    note=args.note,
                    current_status=status,
                )
                relationships_approved += rel_stats.relationships_approved
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    print(
        f"Approved by confidence interval [{args.min_confidence:.2f}, {args.max_confidence:.2f}]:"
    )
    print(f"  Objects approved: {fmt(objects_approved)}")
    print(f"  Relationships approved: {fmt(relationships_approved)}")


def register(sub: argparse._SubParsersAction) -> None:
    show_obj = sub.add_parser("show-object")
    show_obj.add_argument("object_id")
    show_obj.set_defaults(func=_cmd_show_object)

    search_k = sub.add_parser("search-knowledge")
    search_k.add_argument("query")
    search_k.set_defaults(func=_cmd_search_knowledge)

    sub.add_parser("review-candidates").set_defaults(func=_cmd_review_candidates)

    approve = sub.add_parser("approve-object")
    approve.add_argument("object_id")
    approve.set_defaults(func=lambda args: _cmd_review_object(args, ReviewState.APPROVED.value))

    reject = sub.add_parser("reject-object")
    reject.add_argument("object_id")
    reject.set_defaults(func=lambda args: _cmd_review_object(args, ReviewState.REJECTED.value))

    approve_rel = sub.add_parser("approve-relationship")
    approve_rel.add_argument("relationship_id", type=int)
    approve_rel.set_defaults(
        func=lambda args: _cmd_review_relationship(args, ReviewState.APPROVED.value)
    )

    reject_rel = sub.add_parser("reject-relationship")
    reject_rel.add_argument("relationship_id", type=int)
    reject_rel.set_defaults(
        func=lambda args: _cmd_review_relationship(args, ReviewState.REJECTED.value)
    )

    approve_interval = sub.add_parser("approve-confidence-interval")
    approve_interval.add_argument("--min-confidence", type=float, required=True)
    approve_interval.add_argument("--max-confidence", type=float, required=True)
    approve_interval.add_argument(
        "--target", choices=("objects", "relationships", "all"), default="all"
    )
    approve_interval.add_argument(
        "--include-reviewed",
        action="store_true",
        help="also approve rows that are already in a REVIEWED state",
    )
    approve_interval.add_argument("--note", default="")
    approve_interval.set_defaults(func=_cmd_approve_confidence_interval)


__all__ = ["register"]
