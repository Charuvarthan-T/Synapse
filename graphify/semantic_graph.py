"""Integrates validated semantic relations into an existing code graph.

Structural edges are never overwritten: when a semantic relation targets a
node pair that already has an edge, the relation is recorded as a secondary
annotation on that edge rather than replacing its primary ``relation``. Pairs
with no existing edge get a genuine new graph edge, which is what lets
weighted and community retrieval traverse semantic-only connections.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from graphify.semantic_relations import SemanticExtractionResult, SemanticRelation

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
