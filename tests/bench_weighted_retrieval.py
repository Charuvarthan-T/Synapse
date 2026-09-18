#!/usr/bin/env python3
"""Lightweight benchmark: unweighted vs relationship-aware retrieval.

Measures only observable quantities on a synthetic diamond graph:
traversal time, hops, nodes visited, and path relation score.
Does not fabricate accuracy claims.

Usage:
    uv run --frozen python tests/bench_weighted_retrieval.py
"""

from __future__ import annotations

import statistics
import time

import networkx as nx

from graphify.weighted_retrieval import (
    compare_path_modes,
    expand_neighborhood,
    path_relation_score,
)


def _build_corpus(n_noise: int = 80) -> nx.DiGraph:
    """Strong multi-hop call path vs a cheap weak direct import, plus noise."""
    G = nx.DiGraph()
    for nid, label in [("A", "Alpha"), ("B", "Beta"), ("C", "Gamma"), ("D", "Delta")]:
        G.add_node(nid, label=label, source_file=f"{nid}.py")
    # Weak but short: unweighted hop-shortest prefers this.
    G.add_edge("A", "D", relation="imports", confidence="EXTRACTED")
    # Strong but longer: weighted cost-shortest prefers this.
    G.add_edge("A", "B", relation="calls", confidence="EXTRACTED")
    G.add_edge("B", "D", relation="calls", confidence="EXTRACTED")
    G.add_edge("A", "C", relation="imports", confidence="EXTRACTED")
    G.add_edge("C", "D", relation="imports", confidence="EXTRACTED")
    for i in range(n_noise):
        nid = f"N{i}"
        G.add_node(nid, label=f"Noise{i}", source_file=f"n{i}.py")
        G.add_edge("A", nid, relation="imports", confidence="INFERRED")
        G.add_edge(nid, "D", relation="imports", confidence="INFERRED")
    return G


def _time_expand(G: nx.DiGraph, weighted: bool, rounds: int = 40) -> list[float]:
    samples: list[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        expand_neighborhood(G, ["A"], 2, mode="bfs", weighted=weighted)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def main() -> None:
    G = _build_corpus()
    comparison = compare_path_modes(G, "A", "D")
    uw = comparison["unweighted"]
    wt = comparison["weighted"]

    print("=== Relationship-aware weighted retrieval benchmark ===")
    print(f"graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print()
    print("Path A -> D")
    print(
        f"  unweighted: path={uw['path']} hops={uw['hops']} "
        f"relation_score={uw['relation_score']:.4f} "
        f"time_ms={uw['stats'].elapsed_ms:.3f}"
    )
    print(
        f"  weighted:   path={wt['path']} hops={wt['hops']} "
        f"relation_score={wt['relation_score']:.4f} "
        f"time_ms={wt['stats'].elapsed_ms:.3f}"
    )
    print()

    uw_times = _time_expand(G, weighted=False)
    wt_times = _time_expand(G, weighted=True)
    uw_trav = expand_neighborhood(G, ["A"], 2, mode="bfs", weighted=False)
    wt_trav = expand_neighborhood(G, ["A"], 2, mode="bfs", weighted=True)

    print("BFS expand from A (depth=2)")
    print(
        f"  unweighted: nodes={uw_trav.stats.nodes_visited} "
        f"edges={uw_trav.stats.edges_traversed} "
        f"explored={uw_trav.stats.nodes_explored} "
        f"median_ms={statistics.median(uw_times):.3f}"
    )
    print(
        f"  weighted:   nodes={wt_trav.stats.nodes_visited} "
        f"edges={wt_trav.stats.edges_traversed} "
        f"explored={wt_trav.stats.nodes_explored} "
        f"median_ms={statistics.median(wt_times):.3f}"
    )
    print()
    print("Retrieval ordering (first 5 non-seed edges discovered)")
    print(f"  unweighted edges: {uw_trav.edges[:5]}")
    print(f"  weighted edges:   {wt_trav.edges[:5]}")
    print()
    if wt["path"] and uw["path"]:
        delta = path_relation_score(G, wt["path"]) - path_relation_score(G, uw["path"])
        print(f"Path quality delta (weighted - unweighted relation score): {delta:+.4f}")


if __name__ == "__main__":
    main()
