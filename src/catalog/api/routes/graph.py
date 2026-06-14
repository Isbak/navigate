"""Graph endpoints: nodes, edges, neighbors, impact, path, and a combined export.

The neighbor/impact/path operations run over the *approved* knowledge graph,
built in-memory from SQLite via the same GraphClient/NetworkX path the CLI uses,
so no Fuseki server is required.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ...graph import network
from ...graph.client import GraphClient
from ...knowledge import repository as know_repo
from ...knowledge.export import build_edges, build_nodes
from .. import serializers
from ..config import ApiSettings
from ..dependencies import get_db, get_settings
from ..errors import not_found
from ..pagination import Pagination, pagination_params
from ..schemas import (
    GraphEdge,
    GraphExport,
    GraphNeighbor,
    GraphNode,
    ImpactItem,
    ImpactResponse,
    NeighborsResponse,
    PaginatedResponse,
    PathHop,
    PathResponse,
)

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/nodes", response_model=PaginatedResponse[GraphNode])
def nodes(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
) -> PaginatedResponse[GraphNode]:
    all_nodes = build_nodes(conn)
    window = all_nodes[page.offset : page.offset + page.limit]
    items = [serializers.graph_node(n) for n in window]
    return PaginatedResponse(
        items=items, limit=page.limit, offset=page.offset, total=len(all_nodes)
    )


@router.get("/edges", response_model=PaginatedResponse[GraphEdge])
def edges(
    conn: sqlite3.Connection = Depends(get_db),
    page: Pagination = Depends(pagination_params),
) -> PaginatedResponse[GraphEdge]:
    all_edges = build_edges(conn)
    window = all_edges[page.offset : page.offset + page.limit]
    items = [serializers.graph_edge(e) for e in window]
    return PaginatedResponse(
        items=items, limit=page.limit, offset=page.offset, total=len(all_edges)
    )


@router.get("/object/{object_id}/neighbors", response_model=NeighborsResponse)
def neighbors(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> NeighborsResponse:
    _require_object(conn, object_id)
    graph = _build_graph(conn, settings)
    grouped = network.neighbors(graph, object_id)
    neighbors_out = {
        predicate: [GraphNeighbor(**item) for item in items]
        for predicate, items in grouped.items()
    }
    return NeighborsResponse(object_id=object_id, neighbors=neighbors_out)


@router.get("/object/{object_id}/impact", response_model=ImpactResponse)
def impact(
    object_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> ImpactResponse:
    _require_object(conn, object_id)
    graph = _build_graph(conn, settings)
    grouped = network.impact(graph, object_id)
    impact_out = {
        otype: [ImpactItem(**item) for item in items]
        for otype, items in grouped.items()
    }
    return ImpactResponse(object_id=object_id, impact=impact_out)


@router.get("/path", response_model=PathResponse)
def path(
    source: str = Query(...),
    target: str = Query(...),
    max_depth: int | None = Query(None, ge=1, le=10),
    conn: sqlite3.Connection = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> PathResponse:
    graph = _build_graph(conn, settings)
    hops = network.shortest_path(graph, source, target)
    if hops is None or (max_depth is not None and len(hops) > max_depth):
        return PathResponse(source=source, target=target, found=False, hops=[])
    return PathResponse(
        source=source,
        target=target,
        found=True,
        hops=[PathHop(**hop) for hop in hops],
    )


@router.get("/export-json", response_model=GraphExport)
def export_json(conn: sqlite3.Connection = Depends(get_db)) -> GraphExport:
    return GraphExport(
        nodes=[serializers.graph_node(n) for n in build_nodes(conn)],
        edges=[serializers.graph_edge(e) for e in build_edges(conn)],
    )


def _build_graph(conn: sqlite3.Connection, settings: ApiSettings):
    client = GraphClient.from_sqlite(conn, queries_dir=settings.queries_dir)
    return network.build_digraph(client)


def _require_object(conn: sqlite3.Connection, object_id: str) -> None:
    if know_repo.get_object(conn, object_id) is None:
        raise not_found("Knowledge object not found", object_id=object_id)
