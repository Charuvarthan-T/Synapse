"""Tests for relationship-aware weighted graph retrieval."""

from __future__ import annotations

import networkx as nx
import pytest

from graphify.serve import _bfs, _dfs, _query_graph_text, _subgraph_to_text
from graphify.weighted_retrieval import (
    compare_path_modes,
    expand_neighborhood,
    find_shortest_path,
    path_relation_score,
    rank_edges_for_retrieval,
    rank_nodes_for_retrieval,
)
from graphify.weights import (
    DEFAULT_RELATION_IMPORTANCE,
    RelationWeightRegistry,
    edge_cost,
    edge_importance,
    get_default_registry,
    importance_to_cost,
    relation_cost,
    relation_importance,
)


def _diamond_graph() -> nx.DiGraph:
    """A --calls--> B --imports--> D  and  A --imports--> C --calls--> D.

    Unweighted: both paths are 2 hops.
    Weighted: prefers A→B→D (calls then imports) over A→C→D only if
    cumulative cost of calls+imports beats imports+calls — they are equal
    costs swapped. Use asymmetric side paths instead.
    """
    G = nx.DiGraph()
    for nid, label in [("A", "Alpha"), ("B", "Beta"), ("C", "Gamma"), ("D", "Delta")]:
        G.add_node(nid, label=label, source_file=f"{nid}.py", source_location="L1")
    # Strong path: A -calls-> B -calls-> D
    G.add_edge("A", "B", relation="calls", confidence="EXTRACTED")
    G.add_edge("B", "D", relation="calls", confidence="EXTRACTED")
    # Weak path: A -imports-> C -imports-> D
    G.add_edge("A", "C", relation="imports", confidence="EXTRACTED")
    G.add_edge("C", "D", relation="imports", confidence="EXTRACTED")
    return G


def _ranking_graph() -> nx.Graph:
    G = nx.Graph()
    for nid in ("seed", "via_call", "via_import"):
        G.add_node(nid, label=nid, source_file=f"{nid}.py")
    G.add_edge("seed", "via_call", relation="calls", confidence="EXTRACTED")
    G.add_edge("seed", "via_import", relation="imports", confidence="EXTRACTED")
    return G


# --- weights registry -------------------------------------------------------


def test_default_relation_ordering():
    assert relation_importance("calls") > relation_importance("inherits")
    assert relation_importance("inherits") > relation_importance("implements")
    assert relation_importance("implements") > relation_importance("references")
    assert relation_importance("references") > relation_importance("imports")


def test_unknown_relation_falls_back():
    reg = get_default_registry()
    assert relation_importance("totally_novel_edge") == reg.unknown_importance
    assert relation_importance(None) == reg.unknown_importance
    assert relation_importance("") == reg.unknown_importance


def test_register_extends_scheme():
    reg = RelationWeightRegistry().copy()
    reg.register("my_custom_link", 0.99)
    assert reg.importance("my_custom_link") == pytest.approx(0.99)
    assert reg.traversal_cost("my_custom_link") == pytest.approx(importance_to_cost(0.99))


def test_register_rejects_non_positive():
    reg = RelationWeightRegistry().copy()
    with pytest.raises(ValueError):
        reg.register("bad", 0)
    with pytest.raises(ValueError):
        reg.register("bad", -1)
    with pytest.raises(ValueError):
        reg.register("", 1.0)


def test_confidence_softens_inferred_edges():
    extracted = {"relation": "calls", "confidence": "EXTRACTED"}
    inferred = {"relation": "calls", "confidence": "INFERRED"}
    assert edge_importance(extracted) > edge_importance(inferred)


def test_missing_edge_metadata_defaults():
    assert edge_importance(None) == get_default_registry().unknown_importance
    assert edge_importance({}) == get_default_registry().unknown_importance
    assert edge_cost({}) > 0


def test_relation_weight_override_on_edge():
    data = {"relation": "imports", "relation_weight": 0.95, "confidence": "EXTRACTED"}
    assert edge_importance(data) == pytest.approx(0.95)


def test_cost_is_inverse_of_importance():
    for relation in ("calls", "imports", "inherits"):
        assert relation_cost(relation) == pytest.approx(
            importance_to_cost(relation_importance(relation))
        )


def test_default_map_covers_common_ast_relations():
    for key in ("calls", "imports", "inherits", "implements", "references", "method"):
        assert key in DEFAULT_RELATION_IMPORTANCE


# --- traversal / path -------------------------------------------------------


def test_weighted_path_prefers_strong_relations():
    G = _diamond_graph()
    path, stats = find_shortest_path(G, "A", "D", weighted=True)
    assert path == ["A", "B", "D"]
    assert stats.weighted is True
    assert stats.edges_traversed == 2


def test_unweighted_path_still_finds_two_hop_route():
    G = _diamond_graph()
    path, stats = find_shortest_path(G, "A", "D", weighted=False)
    assert path is not None
    assert len(path) - 1 == 2
    assert stats.weighted is False


def test_disconnected_graph_returns_none():
    G = nx.Graph()
    G.add_node("a", label="a")
    G.add_node("b", label="b")
    path, stats = find_shortest_path(G, "a", "b", weighted=True)
    assert path is None
    assert stats.nodes_visited == 0


def test_bfs_weighted_and_unweighted_same_node_set():
    G = _ranking_graph()
    w_nodes, _ = _bfs(G, ["seed"], depth=1, weighted=True)
    u_nodes, _ = _bfs(G, ["seed"], depth=1, weighted=False)
    assert w_nodes == u_nodes == {"seed", "via_call", "via_import"}


def test_weighted_bfs_orders_strong_edges_first():
    G = _ranking_graph()
    result = expand_neighborhood(G, ["seed"], 1, mode="bfs", weighted=True)
    assert result.edges[0] == ("seed", "via_call")
    assert result.reach_importance["via_call"] > result.reach_importance["via_import"]


def test_dfs_weighted_runs_without_error():
    G = _diamond_graph()
    nodes, edges = _dfs(G, ["A"], depth=2, weighted=True)
    assert "D" in nodes
    assert edges


def test_expand_rejects_bad_mode():
    G = _ranking_graph()
    with pytest.raises(ValueError):
        expand_neighborhood(G, ["seed"], 1, mode="sideways")


# --- ranking ----------------------------------------------------------------


def test_rank_nodes_prefers_call_neighbor():
    ordered = rank_nodes_for_retrieval(
        {"seed", "via_call", "via_import"},
        seeds=["seed"],
        distance={"seed": 0, "via_call": 1, "via_import": 1},
        reach_importance={"seed": 1.0, "via_call": 1.0, "via_import": 0.4},
        degree_of=lambda n: 1,
        weighted=True,
    )
    assert ordered[0] == "seed"
    assert ordered[1] == "via_call"
    assert ordered[2] == "via_import"


def test_rank_edges_orders_by_importance():
    G = _ranking_graph()
    edges = [("seed", "via_import"), ("seed", "via_call")]
    ranked = rank_edges_for_retrieval(edges, G, weighted=True)
    assert ranked[0] == ("seed", "via_call")


def test_unweighted_rank_preserves_input_edge_order():
    G = _ranking_graph()
    edges = [("seed", "via_import"), ("seed", "via_call")]
    assert rank_edges_for_retrieval(edges, G, weighted=False) == edges


def test_subgraph_text_lists_call_edge_before_import_when_weighted():
    G = _ranking_graph()
    text = _subgraph_to_text(
        G,
        {"seed", "via_call", "via_import"},
        [("seed", "via_import"), ("seed", "via_call")],
        seeds=["seed"],
        weighted=True,
        reach_importance={"via_call": 1.0, "via_import": 0.4},
        distance={"seed": 0, "via_call": 1, "via_import": 1},
    )
    call_pos = text.index("--calls")
    import_pos = text.index("--imports")
    assert call_pos < import_pos


def test_query_header_mentions_weighted_by_default():
    G = _ranking_graph()
    text = _query_graph_text(G, "seed", mode="bfs", depth=1, weighted=True)
    assert "Weighted relations" in text
    text_u = _query_graph_text(G, "seed", mode="bfs", depth=1, weighted=False)
    assert "Weighted relations" not in text_u


def test_path_relation_score_higher_on_call_path():
    G = _diamond_graph()
    strong = path_relation_score(G, ["A", "B", "D"])
    weak = path_relation_score(G, ["A", "C", "D"])
    assert strong > weak


def test_weighted_path_may_take_more_hops_for_stronger_relations():
    """Unweighted prefers a 1-hop import; weighted prefers 2-hop calls."""
    G = nx.DiGraph()
    for nid in ("A", "B", "D"):
        G.add_node(nid, label=nid)
    G.add_edge("A", "D", relation="imports", confidence="EXTRACTED")
    G.add_edge("A", "B", relation="calls", confidence="EXTRACTED")
    G.add_edge("B", "D", relation="calls", confidence="EXTRACTED")
    weak, _ = find_shortest_path(G, "A", "D", weighted=False)
    strong, _ = find_shortest_path(G, "A", "D", weighted=True)
    assert weak == ["A", "D"]
    assert strong == ["A", "B", "D"]
    assert path_relation_score(G, strong) > path_relation_score(G, weak)


# --- regression compatibility ----------------------------------------------


def test_legacy_bfs_signature_still_works():
    G = _ranking_graph()
    visited, edges = _bfs(G, ["seed"], 1)
    assert "via_call" in visited
    assert isinstance(edges, list)


def test_empty_graph_traversal():
    G = nx.Graph()
    result = expand_neighborhood(G, [], 2, weighted=True)
    assert result.nodes == set()
    assert result.edges == []
