"""Integrates validated semantic relations into an existing code graph.

Structural edges are never overwritten: when a semantic relation targets a
node pair that already has an edge, the relation is recorded as a secondary
annotation on that edge rather than replacing its primary ``relation``. Pairs
with no existing edge get a genuine new graph edge, which is what lets
weighted and community retrieval traverse semantic-only connections.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import networkx as nx

from graphify.semantic_relations import SEMANTIC_ORIGIN, SemanticExtractionResult, SemanticRelation

SECONDARY_RELATIONS_KEY = "secondary_relations"
_MAX_SECONDARY_RELATIONS_PER_EDGE = 8


@dataclass(frozen=True)
class SemanticIntegrationReport:
    """Summary of how semantic relations were applied to a graph."""

    edges_added: int
    edges_augmented: int
    concept_nodes_added: int
    relations_skipped: int


def _edge_attrs(G: nx.Graph, u: str, v: str) -> dict:
    """Return one mutable attribute dict for edge (u, v), tolerating MultiGraph."""
    raw = G[u][v]
    if G.is_multigraph():
        return next(iter(raw.values()))
    return raw


def _existing_edge_endpoints(G: nx.Graph, source: str, target: str) -> tuple[str, str] | None:
    if G.has_edge(source, target):
        return source, target
    if not G.is_directed() and G.has_edge(target, source):
        return target, source
    return None


def _augment_existing_edge(
    G: nx.Graph,
    u: str,
    v: str,
    relation: SemanticRelation,
    source_file: str,
) -> bool:
    """Append ``relation`` to an existing edge's secondary-relation list.

    Returns ``False`` when the relation type is already present or the
    per-edge cap is reached, so repeated extraction runs stay idempotent.
    """
    data = _edge_attrs(G, u, v)
    secondary: list[dict] = data.setdefault(SECONDARY_RELATIONS_KEY, [])
    if any(item.get("relation") == relation.relation for item in secondary):
        return False
    if len(secondary) >= _MAX_SECONDARY_RELATIONS_PER_EDGE:
        return False
    secondary.append(relation.to_edge_attrs(source_file=source_file))
    return True


def apply_semantic_relations(
    G: nx.Graph,
    result: SemanticExtractionResult,
    *,
    default_source_file: str = "",
) -> SemanticIntegrationReport:
    """Merge validated semantic relations into ``G`` in place.

    Concept nodes referenced by relations but absent from ``G`` are added
    first so every relation resolves to a valid endpoint. Existing structural
    edges (``calls``, ``imports``, ``inherits``, ...) are preserved; new
    graph edges are only created for previously unconnected node pairs.
    """
    concept_nodes_added = 0
    for node in result.concept_nodes:
        if node["id"] not in G:
            G.add_node(node["id"], **node)
            concept_nodes_added += 1

    edges_added = 0
    edges_augmented = 0
    relations_skipped = 0
    for relation in result.relations:
        if relation.source not in G or relation.target not in G:
            relations_skipped += 1
            continue
        source_file = default_source_file or str(G.nodes[relation.source].get("source_file") or "")
        existing = _existing_edge_endpoints(G, relation.source, relation.target)
        if existing is not None:
            u, v = existing
            if _augment_existing_edge(G, u, v, relation, source_file):
                edges_augmented += 1
            else:
                relations_skipped += 1
            continue
        attrs = relation.to_edge_attrs(source_file=source_file)
        attrs["_src"] = relation.source
        attrs["_tgt"] = relation.target
        G.add_edge(relation.source, relation.target, **attrs)
        edges_added += 1

    return SemanticIntegrationReport(
        edges_added=edges_added,
        edges_augmented=edges_augmented,
        concept_nodes_added=concept_nodes_added,
        relations_skipped=relations_skipped,
    )


def _pair(edge: dict) -> frozenset:
    return frozenset((edge.get("source"), edge.get("target")))


def _promote(secondary: list[dict]) -> dict:
    """Edge attributes for annotations whose structural carrier edge is gone:
    the first relation becomes the edge, the rest stay secondary (mirrors
    apply_semantic_relations for unconnected pairs)."""
    first, *rest = secondary
    attrs = dict(first)
    attrs.setdefault("_origin", SEMANTIC_ORIGIN)
    if rest:
        attrs[SECONDARY_RELATIONS_KEY] = [dict(item) for item in rest]
    return attrs


def restore_semantic_edges(
    previous_edges: Iterable[dict], edges: list[dict], node_ids: set
) -> list[dict]:
    """Carry the semantic layer of a previous graph onto freshly extracted edges.

    Rebuilding the code graph from source regenerates structural edges and
    drops edges attributed to re-extracted files, which would silently discard
    semantic relations (and their retrieval weight) although the code units
    they connect still exist. Annotations belong to a node pair, so they are
    re-attached to whatever structural edge now joins that pair; if none does,
    they become a semantic edge. Mutates ``edges`` in place and returns the
    extra edges to add. Relations with a missing endpoint are dropped.
    """
    by_pair: dict[frozenset, list[dict]] = {}
    for edge in edges:
        by_pair.setdefault(_pair(edge), []).append(edge)
    extra: list[dict] = []
    for edge in previous_edges:
        u, v = edge.get("source"), edge.get("target")
        if u is None or v is None or u not in node_ids or v not in node_ids:
            continue
        pair = _pair(edge)
        if edge.get("_origin") == SEMANTIC_ORIGIN:
            if pair not in by_pair:
                extra.append(dict(edge))
                by_pair[pair] = [extra[-1]]
            continue
        secondary = edge.get(SECONDARY_RELATIONS_KEY)
        if not secondary:
            continue
        carriers = by_pair.get(pair)
        if carriers:
            for carrier in carriers:
                if not carrier.get(SECONDARY_RELATIONS_KEY):
                    carrier[SECONDARY_RELATIONS_KEY] = [dict(item) for item in secondary]
        else:
            extra.append({"source": u, "target": v, **_promote(secondary)})
            by_pair[pair] = [extra[-1]]
    return extra


def restore_semantic_layer(G: nx.Graph, previous_edges: Iterable[dict]) -> int:
    """Graph-level :func:`restore_semantic_edges`: re-apply a previous graph's
    semantic relations to ``G`` in place. Returns the number of relations
    restored (new semantic edges plus re-attached annotations)."""
    restored = 0
    for edge in previous_edges:
        u, v = edge.get("source"), edge.get("target")
        if u is None or v is None or u not in G or v not in G:
            continue
        ends = (u, v) if G.has_edge(u, v) else (v, u) if G.has_edge(v, u) else None
        if edge.get("_origin") == SEMANTIC_ORIGIN:
            if ends is None:
                G.add_edge(u, v, **{k: val for k, val in edge.items() if k not in ("source", "target")})
                restored += 1
            continue
        secondary = edge.get(SECONDARY_RELATIONS_KEY)
        if not secondary:
            continue
        if ends is None:
            G.add_edge(u, v, **_promote(secondary))
            restored += len(secondary)
            continue
        data = _edge_attrs(G, *ends)
        if not data.get(SECONDARY_RELATIONS_KEY):
            data[SECONDARY_RELATIONS_KEY] = [dict(item) for item in secondary]
            restored += len(secondary)
    return restored
