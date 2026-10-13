"""Production multigraph representation for structural and semantic edges.

A NetworkX ``MultiDiGraph`` keeps every distinct relation between the same
node pair as its own edge, with its own confidence, provenance, and metadata.
Older graphs that stored semantic relations in ``secondary_relations`` are
expanded by :func:`ensure_multigraph`. Simple graphs with one edge per pair
stay readable: one edge becomes one keyed edge.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from graphify.semantic_graph import SECONDARY_RELATIONS_KEY


def _copy_graph_attrs(src: nx.Graph, dest: nx.Graph) -> None:
    dest.graph.update(dict(src.graph))


def explode_secondary(G: nx.MultiDiGraph) -> int:
    """Turn ``secondary_relations`` annotations into parallel edges. In place."""
    added = 0
    pending: list[tuple[Any, Any, dict]] = []
    for u, v, key, data in list(G.edges(keys=True, data=True)):
        secondary = data.get(SECONDARY_RELATIONS_KEY)
        if not isinstance(secondary, list) or not secondary:
            continue
        data.pop(SECONDARY_RELATIONS_KEY, None)
        for item in secondary:
            if isinstance(item, dict) and item.get("relation"):
                pending.append((u, v, dict(item)))
    for u, v, attrs in pending:
        if _has_same_relation(G, u, v, attrs):
            continue
        G.add_edge(u, v, **attrs)
        added += 1
    return added


def _has_same_relation(G: nx.MultiDiGraph, u: str, v: str, attrs: dict) -> bool:
    if not G.has_edge(u, v):
        return False
    relation = attrs.get("relation")
    origin = attrs.get("_origin")
    for data in G[u][v].values():
        if data.get("relation") == relation and data.get("_origin") == origin:
            return True
    return False


def ensure_multigraph(G: nx.Graph) -> nx.MultiDiGraph:
    """Return a ``MultiDiGraph`` that preserves every relation as its own edge."""
    if isinstance(G, nx.MultiDiGraph):
        explode_secondary(G)
        return G
    multi = nx.MultiDiGraph()
    multi.add_nodes_from(G.nodes(data=True))
    _copy_graph_attrs(G, multi)
    for u, v, data in G.edges(data=True):
        payload = dict(data)
        secondary = payload.pop(SECONDARY_RELATIONS_KEY, None)
        multi.add_edge(u, v, **payload)
        if isinstance(secondary, list):
            for item in secondary:
                if isinstance(item, dict) and item.get("relation"):
                    multi.add_edge(u, v, **dict(item))
    return multi


def relation_edges(G: nx.Graph, u: str, v: str) -> list[dict]:
    """Every attribute dict on the directed pair, for simple graphs and multigraphs."""
    if not G.has_edge(u, v):
        return []
    raw = G[u][v]
    if G.is_multigraph():
        return [dict(item) for item in raw.values()]
    return [dict(raw)]


def graph_to_node_link(G: nx.Graph) -> dict:
    """Serialize with the multigraph flag when ``G`` has parallel edges."""
    from networkx.readwrite import json_graph

    data = json_graph.node_link_data(G, edges="links")
    data["directed"] = True
    data["multigraph"] = bool(G.is_multigraph())
    data["synapse_graph_format"] = 2 if G.is_multigraph() else 1
    return data


def graph_from_node_link(data: dict) -> nx.Graph:
    """Load either a legacy simple graph or a Synapse multigraph."""
    from networkx.readwrite import json_graph

    payload = dict(data)
    if "links" not in payload and "edges" in payload:
        payload["links"] = payload["edges"]
    try:
        G = json_graph.node_link_graph(payload, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(payload)
    if payload.get("multigraph") or _any_secondary(G):
        return ensure_multigraph(G)
    return G


def _any_secondary(G: nx.Graph) -> bool:
    for _, _, data in G.edges(data=True):
        secondary = data.get(SECONDARY_RELATIONS_KEY)
        if isinstance(secondary, list) and secondary:
            return True
    return False
