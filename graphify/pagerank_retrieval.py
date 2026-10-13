"""Personalized PageRank retrieval over a relation-weighted code graph.

Transition weight of an edge is its relation importance (higher is more
likely). Parallel multigraph edges contribute their strongest importance, so
a ``calls`` edge is not diluted by a weaker ``imports`` edge on the same pair.
The personalization vector is uniform over the query seed nodes.

NetworkX power iteration is deterministic for a fixed graph, seed set, damping
factor, tolerance, and iteration cap. Disconnected nodes still receive
teleport mass. Dangling nodes (no outgoing edges) are handled by NetworkX.
An empty seed set returns no scores rather than a uniform walk over the graph.
"""

from __future__ import annotations

import time
from typing import Iterable

import networkx as nx

from graphify.build import edge_datas
from graphify.weighted_retrieval import TraversalResult, TraversalStats
from graphify.weights import RelationWeightRegistry, get_default_registry

DEFAULT_ALPHA = 0.85
DEFAULT_TOL = 1.0e-6
DEFAULT_MAX_ITER = 100


def transition_graph(
    G: nx.Graph,
    registry: RelationWeightRegistry | None = None,
) -> nx.DiGraph:
    """Directed simple graph whose ``weight`` is the strongest parallel relation."""
    reg = registry or get_default_registry()
    host = nx.DiGraph()
    host.add_nodes_from(G.nodes)
    best: dict[tuple[str, str], float] = {}
    for u, v in G.edges():
        datas = edge_datas(G, u, v)
        importance = max((reg.edge_importance(data) for data in datas), default=reg.unknown_importance)
        key = (u, v)
        if importance > best.get(key, 0.0):
            best[key] = importance
    for (u, v), importance in best.items():
        host.add_edge(u, v, weight=importance)
    return host


def personalized_pagerank(
    G: nx.Graph,
    seeds: Iterable[str],
    *,
    registry: RelationWeightRegistry | None = None,
    alpha: float = DEFAULT_ALPHA,
    tol: float = DEFAULT_TOL,
    max_iter: int = DEFAULT_MAX_ITER,
) -> dict[str, float]:
    """Return a deterministic Personalized PageRank score for every node.

    ``seeds`` that are not in ``G`` are ignored. No remaining seeds yields
    an empty mapping.
    """
    personalization_nodes = [node for node in dict.fromkeys(seeds) if node in G]
    if not personalization_nodes or G.number_of_nodes() == 0:
        return {}
    host = transition_graph(G, registry)
    share = 1.0 / len(personalization_nodes)
    personalization = {node: share for node in personalization_nodes}
    if host.number_of_edges() == 0:
        return {node: (share if node in personalization else 0.0) for node in host.nodes}
    scores = nx.pagerank(
        host,
        alpha=float(alpha),
        personalization=personalization,
        weight="weight",
        tol=float(tol),
        max_iter=int(max_iter),
    )
    return {node: float(score) for node, score in scores.items()}


def rank_by_pagerank(scores: dict[str, float]) -> list[str]:
    """Highest score first. Ties break by node id so the order is stable."""
    return sorted(scores, key=lambda node: (-scores[node], str(node)))


def ppr_traversal(
    G: nx.Graph,
    seeds: list[str],
    *,
    registry: RelationWeightRegistry | None = None,
    alpha: float = DEFAULT_ALPHA,
    tol: float = DEFAULT_TOL,
    max_iter: int = DEFAULT_MAX_ITER,
    node_limit: int = 40,
) -> TraversalResult:
    """Rank a neighborhood with Personalized PageRank and return a traversal result.

    The result uses the same :class:`TraversalResult` shape as weighted BFS so
    the existing context formatter can render it. ``reach_importance`` carries
    the PageRank score.
    """
    t0 = time.perf_counter()
    scores = personalized_pagerank(
        G, seeds, registry=registry, alpha=alpha, tol=tol, max_iter=max_iter
    )
    ranked = rank_by_pagerank(scores)
    seed_set = set(seeds)
    chosen = [node for node in ranked if node in seed_set]
    for node in ranked:
        if node not in seed_set:
            chosen.append(node)
        if len(chosen) >= node_limit:
            break
    chosen_set = set(chosen)
    edges: list[tuple[str, str]] = []
    for u, v in G.edges():
        if u in chosen_set and v in chosen_set:
            edges.append((u, v))
    elapsed = (time.perf_counter() - t0) * 1000
    return TraversalResult(
        nodes=chosen_set,
        edges=edges,
        stats=TraversalStats(
            nodes_visited=len(chosen_set),
            edges_traversed=len(edges),
            nodes_explored=G.number_of_nodes(),
            elapsed_ms=elapsed,
            weighted=True,
            mode="ppr",
        ),
        reach_importance={node: scores.get(node, 0.0) for node in chosen_set},
        distance={node: index for index, node in enumerate(chosen)},
    )
