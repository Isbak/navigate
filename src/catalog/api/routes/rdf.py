"""RDF endpoints: projection stats, in-memory export, and validation.

These project the *approved* knowledge graph into RDF using the same builders as
the ``rdf-export`` CLI. ``stats`` and ``export`` are side-effect free (the export
serialises in memory rather than writing files); ``validate`` re-parses whatever
a prior ``rdf-export`` wrote under ``exports/rdf/``.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...rdf.export import FORMATS, build_graphs, rdf_stats, validate_rdf
from ..dependencies import get_db
from ..errors import bad_request
from ..schemas import RdfStats, RdfValidation

router = APIRouter(prefix="/rdf", tags=["rdf"])

# Media type to advertise per rdflib serializer name.
_MEDIA_TYPES = {
    "turtle": "text/turtle",
    "json-ld": "application/ld+json",
    "nt": "application/n-triples",
}


@router.get("/stats", response_model=RdfStats)
def stats(conn: sqlite3.Connection = Depends(get_db)) -> RdfStats:
    """Counts of what an RDF export would contain (objects/relationships/evidence)."""

    return RdfStats(**rdf_stats(conn))


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/turtle": {}}, "description": "Serialised RDF graph."}},
)
def export(
    conn: sqlite3.Connection = Depends(get_db),
    fmt: str = Query("turtle", description="turtle | json-ld | nt (and aliases)"),
) -> Response:
    """Serialise the combined approved graph to RDF in the requested format.

    Returns the RDF as a downloadable body rather than the JSON envelope, since
    the payload is a single serialised document.
    """

    if fmt not in FORMATS:
        raise bad_request(
            f"Unsupported format {fmt!r}.",
            choices=sorted(FORMATS),
        )
    serializer, ext = FORMATS[fmt]

    graphs = build_graphs(conn)
    combined = graphs["knowledge"]
    for name, graph in graphs.items():
        if name != "knowledge":
            combined += graph
    body = combined.serialize(format=serializer)
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    return Response(
        content=body,
        media_type=_MEDIA_TYPES.get(serializer, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="navigate.{ext}"'},
    )


@router.get("/validate", response_model=RdfValidation)
def validate() -> RdfValidation:
    """Re-parse every file written by a prior ``rdf-export`` and report results."""

    return RdfValidation(files=validate_rdf())
