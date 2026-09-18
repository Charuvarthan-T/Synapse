"""Relationship-aware traversal, path finding, and retrieval ranking.

Builds on :mod:`graphify.weights` so callers never hardcode relation scores.
Unweighted mode mirrors legacy Graphify hop-count BFS/DFS/shortest-path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

import networkx as nx

from graphify.build import edge_datas
from graphify.weights import RelationWeightRegistry, get_default_registry


@dataclass(frozen=True)
class TraversalStats:
    """Lightweight counters for benchmarks (no fabricated metrics)."""

    nodes_visited: int
    edges_traversed: int
    nodes_explored: int
    elapsed_ms: float
    weighted: bool
    mode: str


@dataclass
class TraversalResult:
    nodes: set[str]
    edges: list[tuple[str, str]]
    stats: TraversalStats
    # Best edge importance used to reach each non-seed node (for ranking).
    reach_importance: dict[str, float] = field(default_factory=dict)
    # Shortest hop distance from any seed.
    distance: dict[str, int] = field(default_factory=dict)


def _hub_threshold(G: nx.Graph) -> int:
    degrees = [G.degree(n) for n in G.nodes()]
    if not degrees:
        return 50
    degrees_sorted = sorted(degrees)
    p99_idx = int(len(degrees_sorted) * 0.99)
    return max(50, degrees_sorted[p99_idx])


def _neighbor_edge_payloads(
    G: nx.Graph,
    node: str,
) -> list[tuple[str, list[dict]]]:
    """Return (neighbor, edge-attr-dicts) using legacy ``G.neighbors`` adjacency.

    On ``DiGraph`` this is successors only — matching pre-weight BFS/DFS so
    hop-sets stay backward compatible. Edge payloads tolerate either orientation.
    """
    out: list[tuple[str, list[dict]]] = []
    for nbr in G.neighbors(node):
        if G.has_edge(node, nbr):
            datas = edge_datas(G, node, nbr)
        elif G.has_edge(nbr, node):
            datas = edge_datas(G, nbr, node)
        else:
            datas = []
        out.append((nbr, datas))
    return out


def _best_edge_meta(
    datas: Iterable[dict],
    registry: RelationWeightRegistry,
) -> tuple[float, float]:
    """Return (importance, cost) for the strongest parallel edge."""
    best_imp = registry.unknown_importance
    best_cost = registry.edge_cost(None)
    found = False
    for data in datas:
        imp = registry.edge_importance(data)
        cost = registry.edge_cost(data)
        if not found or imp > best_imp:
            best_imp = imp
            best_cost = cost
            found = True
    return best_imp, best_cost


def build_undirected_cost_graph(
    G: nx.Graph,
    *,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> nx.Graph:
    """Undirected view with ``weight`` = traversal cost (1.0 when unweighted)."""
    reg = registry or get_default_registry()
    und = nx.Graph()
    und.add_nodes_from(G.nodes)
    for u, v in G.edges():
        a, b = (u, v) if u <= v else (v, u)
        if G.has_edge(u, v):
            datas = edge_datas(G, u, v)
        else:
            datas = edge_datas(G, v, u)
        if weighted:
            _, cost = _best_edge_meta(datas, reg)
        else:
            cost = 1.0
        if und.has_edge(a, b):
            prev = float(und[a][b].get("weight", 1.0))
            if cost < prev:
                und[a][b]["weight"] = cost
        else:
            und.add_edge(a, b, weight=cost)
    return und


def find_shortest_path(
    G: nx.Graph,
    source: str,
    target: str,
    *,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> tuple[list[str] | None, TraversalStats]:
    """Hop-shortest (unweighted) or relation-cost-shortest (weighted) path."""
    t0 = time.perf_counter()
    und = build_undirected_cost_graph(G, weighted=weighted, registry=registry)
    try:
        if weighted:
            path = nx.shortest_path(und, source, target, weight="weight")
        else:
            path = nx.shortest_path(und, source, target)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        elapsed = (time.perf_counter() - t0) * 1000.0
        return None, TraversalStats(
            nodes_visited=0,
            edges_traversed=0,
            nodes_explored=0,
            elapsed_ms=elapsed,
            weighted=weighted,
            mode="path",
        )
    elapsed = (time.perf_counter() - t0) * 1000.0
    hops = max(0, len(path) - 1)
    return path, TraversalStats(
        nodes_visited=len(path),
        edges_traversed=hops,
        nodes_explored=len(path),
        elapsed_ms=elapsed,
        weighted=weighted,
        mode="path",
    )


def expand_neighborhood(
    G: nx.Graph,
    start_nodes: list[str],
    depth: int,
    *,
    mode: str = "bfs",
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> TraversalResult:
    """Expand from seeds up to ``depth`` hops.

    Weighted mode still respects hop depth (API-compatible) but expands
    neighbors in relation-importance order and records reach importance for
    ranking. Unweighted mode matches legacy Graphify frontier expansion.
    """
    if mode not in {"bfs", "dfs"}:
        raise ValueError(f"mode must be 'bfs' or 'dfs', got {mode!r}")
    if weighted:
        if mode == "dfs":
            return _weighted_dfs(G, start_nodes, depth, registry=registry)
        return _weighted_bfs(G, start_nodes, depth, registry=registry)
    if mode == "dfs":
        return _unweighted_dfs(G, start_nodes, depth)
    return _unweighted_bfs(G, start_nodes, depth)


def _unweighted_bfs(G: nx.Graph, start_nodes: list[str], depth: int) -> TraversalResult:
    t0 = time.perf_counter()
    hub = _hub_threshold(G)
    seed_set = set(start_nodes)
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: list[tuple[str, str]] = []
    distance = {n: 0 for n in start_nodes}
    explored = 0
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            explored += 1
            if n not in seed_set and G.degree(n) >= hub:
                continue
            for neighbor in G.neighbors(n):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
                    edges_seen.append((n, neighbor))
                    distance.setdefault(neighbor, distance.get(n, 0) + 1)
        visited.update(next_frontier)
        frontier = next_frontier
    elapsed = (time.perf_counter() - t0) * 1000.0
    return TraversalResult(
        nodes=visited,
        edges=edges_seen,
        reach_importance={},
        distance=distance,
        stats=TraversalStats(
            nodes_visited=len(visited),
            edges_traversed=len(edges_seen),
            nodes_explored=explored,
            elapsed_ms=elapsed,
            weighted=False,
            mode="bfs",
        ),
    )


def _unweighted_dfs(G: nx.Graph, start_nodes: list[str], depth: int) -> TraversalResult:
    t0 = time.perf_counter()
    hub = _hub_threshold(G)
    seed_set = set(start_nodes)
    visited: set[str] = set()
    edges_seen: list[tuple[str, str]] = []
    distance: dict[str, int] = {}
    stack = [(n, 0) for n in reversed(start_nodes)]
    explored = 0
    while stack:
        node, d = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        distance[node] = d
        explored += 1
        if node not in seed_set and G.degree(node) >= hub:
            continue
        for neighbor in G.neighbors(node):
            if neighbor not in visited:
                stack.append((neighbor, d + 1))
                edges_seen.append((node, neighbor))
    elapsed = (time.perf_counter() - t0) * 1000.0
    return TraversalResult(
        nodes=visited,
        edges=edges_seen,
        reach_importance={},
        distance=distance,
        stats=TraversalStats(
            nodes_visited=len(visited),
            edges_traversed=len(edges_seen),
            nodes_explored=explored,
            elapsed_ms=elapsed,
            weighted=False,
            mode="dfs",
        ),
    )


def _weighted_bfs(
    G: nx.Graph,
    start_nodes: list[str],
    depth: int,
    *,
    registry: RelationWeightRegistry | None = None,
) -> TraversalResult:
    """Hop-limited expansion; within each hop, prefer stronger relations."""
    t0 = time.perf_counter()
    reg = registry or get_default_registry()
    hub = _hub_threshold(G)
    seed_set = set(start_nodes)
    visited: set[str] = set(start_nodes)
    distance = {n: 0 for n in start_nodes}
    reach_importance = {n: 1.0 for n in start_nodes}
    edges_seen: list[tuple[str, str]] = []
    frontier = list(start_nodes)
    explored = 0

    for _ in range(depth):
        # (neg_importance, cost, parent, neighbor) — strongest relations first.
        candidates: list[tuple[float, float, str, str]] = []
        for n in frontier:
            explored += 1
            if n not in seed_set and G.degree(n) >= hub:
                continue
            for neighbor, datas in _neighbor_edge_payloads(G, n):
                if neighbor in visited:
                    continue
                imp, cost = _best_edge_meta(datas, reg)
                candidates.append((-imp, cost, n, neighbor))
        candidates.sort()
        next_frontier: list[str] = []
        seen_next: set[str] = set()
        for neg_imp, _cost, parent, neighbor in candidates:
            if neighbor in visited or neighbor in seen_next:
                continue
            seen_next.add(neighbor)
            next_frontier.append(neighbor)
            edges_seen.append((parent, neighbor))
            distance[neighbor] = distance.get(parent, 0) + 1
            reach_importance[neighbor] = -neg_imp
        visited.update(seen_next)
        frontier = next_frontier

    elapsed = (time.perf_counter() - t0) * 1000.0
    return TraversalResult(
        nodes=visited,
        edges=edges_seen,
        reach_importance=reach_importance,
        distance=distance,
        stats=TraversalStats(
            nodes_visited=len(visited),
            edges_traversed=len(edges_seen),
            nodes_explored=explored,
            elapsed_ms=elapsed,
            weighted=True,
            mode="bfs",
        ),
    )


def _weighted_dfs(
    G: nx.Graph,
    start_nodes: list[str],
    depth: int,
    *,
    registry: RelationWeightRegistry | None = None,
) -> TraversalResult:
    """DFS that pushes higher-importance neighbors later (popped sooner)."""
    t0 = time.perf_counter()
    reg = registry or get_default_registry()
    hub = _hub_threshold(G)
    seed_set = set(start_nodes)
    visited: set[str] = set()
    edges_seen: list[tuple[str, str]] = []
    distance: dict[str, int] = {}
    reach_importance: dict[str, float] = {}
    # stack entries: (node, depth, reach_imp)
    stack: list[tuple[str, int, float]] = [(n, 0, 1.0) for n in reversed(start_nodes)]
    explored = 0
    while stack:
        node, d, reach_imp = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        distance[node] = d
        reach_importance[node] = reach_imp
        explored += 1
        if node not in seed_set and G.degree(node) >= hub:
            continue
        ranked: list[tuple[float, str, float]] = []
        for neighbor, datas in _neighbor_edge_payloads(G, node):
            if neighbor in visited:
                continue
            imp, _cost = _best_edge_meta(datas, reg)
            ranked.append((imp, neighbor, imp))
        # Push weakest first so strongest are popped first.
        ranked.sort(key=lambda t: t[0])
        for _imp, neighbor, edge_imp in ranked:
            stack.append((neighbor, d + 1, edge_imp))
            edges_seen.append((node, neighbor))
    elapsed = (time.perf_counter() - t0) * 1000.0
    return TraversalResult(
        nodes=visited,
        edges=edges_seen,
        reach_importance=reach_importance,
        distance=distance,
        stats=TraversalStats(
            nodes_visited=len(visited),
            edges_traversed=len(edges_seen),
            nodes_explored=explored,
            elapsed_ms=elapsed,
            weighted=True,
            mode="dfs",
        ),
    )


def rank_nodes_for_retrieval(
    nodes: set[str],
    *,
    seeds: list[str] | None,
    distance: dict[str, int],
    reach_importance: dict[str, float],
    degree_of,
    weighted: bool = True,
) -> list[str]:
    """Stable retrieval ordering: seeds, then hop, then relation strength."""
    seed_list = [n for n in (seeds or []) if n in nodes]
    seed_set = set(seed_list)
    rest = nodes - seed_set
    if weighted:
        ordered_rest = sorted(
            rest,
            key=lambda n: (
                distance.get(n, 1 << 30),
                -float(reach_importance.get(n, 0.0)),
                -int(degree_of(n)),
                str(n),
            ),
        )
    else:
        ordered_rest = sorted(
            rest,
            key=lambda n: (
                distance.get(n, 1 << 30),
                -int(degree_of(n)),
                str(n),
            ),
        )
    return seed_list + ordered_rest


def rank_edges_for_retrieval(
    edges: list[tuple[str, str]],
    G: nx.Graph,
    *,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> list[tuple[str, str]]:
    """Reorder discovery edges by relation importance when weighted."""
    if not weighted or not edges:
        return list(edges)
    reg = registry or get_default_registry()

    def _score(pair: tuple[str, str]) -> tuple[float, str, str]:
        u, v = pair
        try:
            if G.has_edge(u, v):
                datas = edge_datas(G, u, v)
            elif G.has_edge(v, u):
                datas = edge_datas(G, v, u)
            else:
                return (0.0, u, v)
            imp, _ = _best_edge_meta(datas, reg)
            return (imp, u, v)
        except Exception:
            return (0.0, u, v)

    # Stable: sort by -importance, preserve relative order among ties via enumerate.
    indexed = list(enumerate(edges))
    indexed.sort(key=lambda it: (-_score(it[1])[0], it[0]))
    return [e for _, e in indexed]


def path_relation_score(
    G: nx.Graph,
    path: list[str],
    *,
    registry: RelationWeightRegistry | None = None,
) -> float:
    """Geometric-mean importance along a path (1.0 for single-node paths)."""
    if len(path) <= 1:
        return 1.0
    reg = registry or get_default_registry()
    imps: list[float] = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        if G.has_edge(u, v):
            datas = edge_datas(G, u, v)
        elif G.has_edge(v, u):
            datas = edge_datas(G, v, u)
        else:
            imps.append(reg.unknown_importance)
            continue
        imp, _ = _best_edge_meta(datas, reg)
        imps.append(imp)
    if not imps:
        return reg.unknown_importance
    product = 1.0
    for imp in imps:
        product *= imp
    return product ** (1.0 / len(imps))


def compare_path_modes(
    G: nx.Graph,
    source: str,
    target: str,
    *,
    registry: RelationWeightRegistry | None = None,
) -> dict:
    """Benchmark helper: unweighted vs weighted path on the same endpoints."""
    unweighted_path, unweighted_stats = find_shortest_path(
        G, source, target, weighted=False, registry=registry
    )
    weighted_path, weighted_stats = find_shortest_path(
        G, source, target, weighted=True, registry=registry
    )
    return {
        "unweighted": {
            "path": unweighted_path,
            "hops": None if unweighted_path is None else len(unweighted_path) - 1,
            "relation_score": (
                None
                if unweighted_path is None
                else path_relation_score(G, unweighted_path, registry=registry)
            ),
            "stats": unweighted_stats,
        },
        "weighted": {
            "path": weighted_path,
            "hops": None if weighted_path is None else len(weighted_path) - 1,
            "relation_score": (
                None
                if weighted_path is None
                else path_relation_score(G, weighted_path, registry=registry)
            ),
            "stats": weighted_stats,
        },
    }
