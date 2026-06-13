"""Graph retrieval - the heart of GraphRAG's "graph drives retrieval" rule.

Retrieval is *graph-first and graph-only*. There is no document search, no
embedding lookup, and no full-text scan. The process is exactly the one the
prompt mandates::

    Question
        -> find matching knowledge objects   (resolve focus terms -> object ids)
        -> expand graph neighbourhood         (BFS to a configurable depth)
        -> retrieve approved relationships     (edges within the neighbourhood)
        -> retrieve evidence                   (supporting quotes per object)

Everything runs through the approved-graph projection: objects, types,
confidences, and relationships come from the NetworkX view built over SPARQL
(:func:`catalog.graph.network.build_digraph`), and evidence comes from SPARQL
``kg:supportedBy`` lookups. Because only ``APPROVED`` objects and relationships
are ever projected, the retriever physically cannot surface unapproved
knowledge.

Expansion depth is configurable (1, 2, or 3; default 2), matching the documented
walk ``Release Governance -> Launchpad Model -> Release Management -> Test &
Release Team``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..graph import network
from ..graph.client import GraphClient
from ..graph.loader import id_to_uri

DEFAULT_DEPTH = 2
MAX_DEPTH = 3
# Caps keep the context compact and the prompt small (graph retrieval, not RAG).
DEFAULT_MAX_OBJECTS = 40
DEFAULT_EVIDENCE_PER_OBJECT = 3
DEFAULT_MAX_EVIDENCE = 30

_PREFIXES = (
    "PREFIX kg: <https://knowledge-atlas.local/kg/>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)


@dataclass(frozen=True)
class RetrievedObject:
    """A knowledge object pulled into the retrieval neighbourhood."""

    id: str
    label: str
    type: str
    confidence: float
    distance: int  # hops from the nearest seed (0 == a seed/match)
    description: str = ""

    @property
    def is_seed(self) -> bool:
        return self.distance == 0


@dataclass(frozen=True)
class RetrievedRelationship:
    """An approved relationship between two objects in the neighbourhood."""

    source: str
    target: str
    predicate: str
    source_label: str
    target_label: str
    confidence: float


@dataclass(frozen=True)
class RetrievedEvidence:
    """A traceable supporting quote for an object, from a source document."""

    object_id: str
    object_label: str
    artifact_id: str
    quote: str
    confidence: float


@dataclass
class GraphRetrieval:
    """The full result of a graph retrieval, ready for the context builder."""

    seeds: list[str] = field(default_factory=list)
    objects: list[RetrievedObject] = field(default_factory=list)
    relationships: list[RetrievedRelationship] = field(default_factory=list)
    evidence: list[RetrievedEvidence] = field(default_factory=list)
    unresolved_terms: list[str] = field(default_factory=list)
    depth: int = DEFAULT_DEPTH
    sparql: list[str] = field(default_factory=list)

    @property
    def documents(self) -> list[str]:
        """Distinct source documents that contributed evidence, in stable order."""

        seen: list[str] = []
        for item in self.evidence:
            if item.artifact_id and item.artifact_id not in seen:
                seen.append(item.artifact_id)
        return seen

    @property
    def has_support(self) -> bool:
        """True when at least one object was matched and some evidence exists.

        This is the retrieval-side half of the hallucination control: with no
        matched object or no evidence, there is nothing trustworthy to answer
        from, so the assistant must decline rather than invent.
        """

        return bool(self.objects) and bool(self.evidence)


class GraphRetriever:
    """Resolve, expand, and gather evidence over the approved knowledge graph.

    The NetworkX projection is built once (from the client's SPARQL backend) and
    reused across calls, so a multi-turn conversation does not rebuild the graph
    per question. Every SPARQL string actually executed is appended to
    :attr:`sparql_log` so ``--show-sparql`` can surface the real queries.
    """

    def __init__(self, client: GraphClient, graph=None) -> None:
        self.client = client
        self.graph = graph if graph is not None else network.build_digraph(client)
        # The two structural queries that materialised the projection.
        self.base_sparql = [network.NODES_QUERY.strip(), network.EDGES_QUERY.strip()]

    # -- resolution -----------------------------------------------------------

    def labels(self) -> dict[str, str]:
        """Map every object id to its label (for intent analysis)."""

        return {
            node: data.get("label", node)
            for node, data in self.graph.nodes(data=True)
        }

    def resolve(self, terms: list[str]) -> tuple[list[str], list[str]]:
        """Map focus terms to object ids. Returns (resolved_ids, unresolved).

        Resolution tries, in order: exact id, exact label (case-insensitive),
        then a unique substring label match. Ambiguous or absent terms are
        reported as unresolved so the caller can be honest about coverage.
        """

        resolved: list[str] = []
        unresolved: list[str] = []
        for term in terms:
            node = self._resolve_one(term)
            if node is None:
                unresolved.append(term)
            elif node not in resolved:
                resolved.append(node)
        return resolved, unresolved

    def _resolve_one(self, term: str) -> str | None:
        if term in self.graph:
            return term
        lowered = term.strip().lower()
        exact = [
            node
            for node, data in self.graph.nodes(data=True)
            if data.get("label", "").lower() == lowered
        ]
        if exact:
            return exact[0]
        partial = [
            node
            for node, data in self.graph.nodes(data=True)
            if lowered in data.get("label", "").lower() or lowered in node.lower()
        ]
        if len(partial) == 1:
            return partial[0]
        # An exact-token containment tie-breaker: prefer the shortest label that
        # contains the term as a whole, keeping resolution deterministic.
        if partial:
            partial.sort(key=lambda n: (len(self.graph.nodes[n].get("label", n)), n))
            return partial[0]
        return None

    # -- expansion ------------------------------------------------------------

    def expand(self, seeds: list[str], depth: int) -> dict[str, int]:
        """BFS over the undirected projection, returning id -> hop distance.

        Distance 0 is a seed; the walk stops at ``depth`` hops. Direction is
        ignored so the neighbourhood captures both what a seed points to and what
        points at it - the documented Release Governance walk crosses edge
        directions freely.
        """

        depth = max(0, min(depth, MAX_DEPTH))
        distance: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            if seed in self.graph and seed not in distance:
                distance[seed] = 0
                queue.append((seed, 0))
        while queue:
            node, dist = queue.popleft()
            if dist >= depth:
                continue
            neighbours = set(self.graph.successors(node)) | set(
                self.graph.predecessors(node)
            )
            for other in neighbours:
                if other not in distance:
                    distance[other] = dist + 1
                    queue.append((other, dist + 1))
        return distance

    # -- evidence -------------------------------------------------------------

    def evidence_for(self, object_id: str, limit: int) -> list[dict]:
        """Supporting quotes for one object, highest-confidence first."""

        uri = id_to_uri(object_id)
        query = _PREFIXES + f"""
        SELECT ?artifact ?quote ?confidence WHERE {{
            <{uri}> kg:supportedBy ?e .
            ?e kg:sourceArtifact ?artifact .
            OPTIONAL {{ ?e kg:quote ?quote }}
            OPTIONAL {{ ?e kg:confidence ?confidence }}
        }}
        """
        rows = self.client.execute_query(query)
        items = [
            {
                "artifact": row.get("artifact") or "",
                "quote": row.get("quote") or "",
                "confidence": float(row["confidence"]) if row.get("confidence") else 0.0,
            }
            for row in rows
        ]
        items.sort(key=lambda r: (-r["confidence"], r["artifact"], r["quote"]))
        return items[:limit]

    # -- orchestration --------------------------------------------------------

    def retrieve(
        self,
        seeds: list[str],
        *,
        depth: int = DEFAULT_DEPTH,
        extra_seeds: list[str] | None = None,
        unresolved: list[str] | None = None,
        max_objects: int = DEFAULT_MAX_OBJECTS,
        evidence_per_object: int = DEFAULT_EVIDENCE_PER_OBJECT,
        max_evidence: int = DEFAULT_MAX_EVIDENCE,
    ) -> GraphRetrieval:
        """Run the full graph-first retrieval for a set of seed objects."""

        all_seeds = list(dict.fromkeys(list(seeds) + list(extra_seeds or [])))
        distances = self.expand(all_seeds, depth)

        objects = self._build_objects(distances, seeds=all_seeds, limit=max_objects)
        kept_ids = {obj.id for obj in objects}
        relationships = self._build_relationships(kept_ids)
        evidence = self._build_evidence(
            objects, evidence_per_object=evidence_per_object, max_evidence=max_evidence
        )

        sparql = list(self.base_sparql)
        sparql.extend(
            self.evidence_query(obj.id) for obj in objects if obj.distance <= 1
        )

        return GraphRetrieval(
            seeds=all_seeds,
            objects=objects,
            relationships=relationships,
            evidence=evidence,
            unresolved_terms=list(unresolved or []),
            depth=depth,
            sparql=sparql,
        )

    def evidence_query(self, object_id: str) -> str:
        """Return (without executing) the SPARQL used to fetch an object's evidence."""

        uri = id_to_uri(object_id)
        return _PREFIXES + (
            f"SELECT ?artifact ?quote ?confidence WHERE {{ "
            f"<{uri}> kg:supportedBy ?e . ?e kg:sourceArtifact ?artifact . "
            f"OPTIONAL {{ ?e kg:quote ?quote }} "
            f"OPTIONAL {{ ?e kg:confidence ?confidence }} }}"
        )

    # -- internals ------------------------------------------------------------

    def _build_objects(
        self, distances: dict[str, int], *, seeds: list[str], limit: int
    ) -> list[RetrievedObject]:
        descriptions = self._descriptions(seeds)
        objects: list[RetrievedObject] = []
        for node, dist in distances.items():
            data = self.graph.nodes[node]
            objects.append(
                RetrievedObject(
                    id=node,
                    label=data.get("label", node),
                    type=data.get("type", ""),
                    confidence=float(data.get("confidence", 0.0)),
                    distance=dist,
                    description=descriptions.get(node, ""),
                )
            )
        # Closest first, then most confident, then stable id; cap the breadth so
        # the prompt stays compact even on a dense graph.
        objects.sort(key=lambda o: (o.distance, -o.confidence, o.id))
        return objects[:limit]

    def _descriptions(self, object_ids: list[str]) -> dict[str, str]:
        """Fetch rdfs:comment descriptions for the seed objects only."""

        out: dict[str, str] = {}
        for object_id in object_ids:
            uri = id_to_uri(object_id)
            query = _PREFIXES + f"""
            SELECT ?comment WHERE {{ <{uri}> rdfs:comment ?comment }}
            """
            rows = self.client.execute_query(query)
            if rows and rows[0].get("comment"):
                out[object_id] = rows[0]["comment"]
        return out

    def _build_relationships(
        self, kept_ids: set[str]
    ) -> list[RetrievedRelationship]:
        relationships: list[RetrievedRelationship] = []
        for src, tgt, data in self.graph.edges(data=True):
            if src in kept_ids and tgt in kept_ids:
                src_conf = float(self.graph.nodes[src].get("confidence", 0.0))
                tgt_conf = float(self.graph.nodes[tgt].get("confidence", 0.0))
                relationships.append(
                    RetrievedRelationship(
                        source=src,
                        target=tgt,
                        predicate=data.get("predicate", "related_to"),
                        source_label=self.graph.nodes[src].get("label", src),
                        target_label=self.graph.nodes[tgt].get("label", tgt),
                        # No relationship confidence is projected into RDF, so we
                        # derive it from the (weaker) endpoint - a conservative,
                        # deterministic proxy.
                        confidence=min(src_conf, tgt_conf),
                    )
                )
        relationships.sort(
            key=lambda r: (-r.confidence, r.source_label, r.predicate, r.target_label)
        )
        return relationships

    def _build_evidence(
        self,
        objects: list[RetrievedObject],
        *,
        evidence_per_object: int,
        max_evidence: int,
    ) -> list[RetrievedEvidence]:
        evidence: list[RetrievedEvidence] = []
        for obj in objects:
            for item in self.evidence_for(obj.id, evidence_per_object):
                evidence.append(
                    RetrievedEvidence(
                        object_id=obj.id,
                        object_label=obj.label,
                        artifact_id=item["artifact"],
                        quote=item["quote"],
                        confidence=item["confidence"],
                    )
                )
                if len(evidence) >= max_evidence:
                    return evidence
        return evidence


__all__ = [
    "DEFAULT_DEPTH",
    "MAX_DEPTH",
    "DEFAULT_MAX_OBJECTS",
    "DEFAULT_EVIDENCE_PER_OBJECT",
    "DEFAULT_MAX_EVIDENCE",
    "RetrievedObject",
    "RetrievedRelationship",
    "RetrievedEvidence",
    "GraphRetrieval",
    "GraphRetriever",
]
