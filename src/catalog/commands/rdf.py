"""RDF export and Apache Jena Fuseki commands: ``rdf-export``, ``rdf-validate``,
``rdf-stats``, ``fuseki-load``, ``fuseki-clear``."""

from __future__ import annotations

import argparse

from ..db import connect, init_db
from ..rdf.config import load_jena_config
from ..rdf.export import DEFAULT_OUT_DIR as RDF_OUT_DIR
from ..rdf.export import FORMATS as RDF_FORMATS
from ..rdf.export import export_rdf, rdf_stats, validate_rdf
from ..rdf.fuseki import FusekiError, clear_dataset, fuseki_load
from ._common import fmt


def _cmd_rdf_export(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        paths = export_rdf(conn, args.out, fmt=args.format)
        stats = rdf_stats(conn)
    print("RDF export complete:")
    for name in ("ontology", "knowledge", "relationships", "provenance"):
        print(f"  {paths[name]}")
    print(f"\nObjects exported: {fmt(stats['objects'])}")
    print(f"Relationships exported: {fmt(stats['relationships'])}")
    print(f"Evidence exported: {fmt(stats['evidence'])}")


def _cmd_rdf_validate(args: argparse.Namespace) -> None:
    results = validate_rdf(args.out)
    if not results:
        print(f"No RDF files found in {args.out}. Run: catalog rdf-export")
        return
    all_ok = True
    for name, result in results.items():
        if result["ok"]:
            print(f"  OK    {name} ({fmt(result['triples'])} triples)")
        else:
            all_ok = False
            print(f"  FAIL  {name}: {result['error']}")
    print("\nAll files valid." if all_ok else "\nValidation failed.")


def _cmd_rdf_stats(args: argparse.Namespace) -> None:
    init_db(args.db)
    with connect(args.db) as conn:
        stats = rdf_stats(conn)
    print("RDF projection (APPROVED knowledge only):")
    print(f"Objects exported: {fmt(stats['objects'])}")
    print(f"Relationships exported: {fmt(stats['relationships'])}")
    print(f"Evidence exported: {fmt(stats['evidence'])}")
    print(
        f"\nTriples - knowledge: {fmt(stats['knowledge_triples'])}  "
        f"relationships: {fmt(stats['relationship_triples'])}  "
        f"provenance: {fmt(stats['provenance_triples'])}"
    )
    if stats["objects"] == 0:
        print("\nNo approved objects yet. Approve some: catalog approve-object <id>")


def _cmd_fuseki_load(args: argparse.Namespace) -> None:
    config = load_jena_config(args.jena_config)
    print(f"Loading RDF into Fuseki at {config.endpoint} ...")
    try:
        uploaded = fuseki_load(config, args.out)
    except FusekiError as exc:
        print(f"Error: {exc}")
        return
    print("Upload complete:")
    for name in ("ontology", "knowledge", "relationships", "provenance"):
        if name in uploaded:
            print(f"  {name}: {fmt(uploaded[name])} triples")


def _cmd_fuseki_clear(args: argparse.Namespace) -> None:
    config = load_jena_config(args.jena_config)
    try:
        clear_dataset(config)
    except FusekiError as exc:
        print(f"Error: {exc}")
        return
    print(f"Cleared all triples from {config.endpoint}")


def register(sub: argparse._SubParsersAction) -> None:
    rdf_export = sub.add_parser("rdf-export")
    rdf_export.add_argument("--out", default=RDF_OUT_DIR)
    rdf_export.add_argument("--format", default="turtle", choices=sorted(RDF_FORMATS))
    rdf_export.set_defaults(func=_cmd_rdf_export)

    rdf_validate = sub.add_parser("rdf-validate")
    rdf_validate.add_argument("--out", default=RDF_OUT_DIR)
    rdf_validate.set_defaults(func=_cmd_rdf_validate)

    sub.add_parser("rdf-stats").set_defaults(func=_cmd_rdf_stats)

    fuseki = sub.add_parser("fuseki-load")
    fuseki.add_argument("--out", default=RDF_OUT_DIR)
    fuseki.set_defaults(func=_cmd_fuseki_load)

    sub.add_parser("fuseki-clear").set_defaults(func=_cmd_fuseki_clear)


__all__ = ["register"]
