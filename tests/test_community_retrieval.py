"""Tests for hierarchical community-aware code retrieval."""

from __future__ import annotations

import networkx as nx
import pytest

from graphify.community_detection import (
    AutoCommunityDetector,
    LabelPropagationCommunityDetector,
    LouvainCommunityDetector,
    detect_communities,
    get_community_detector,
    partition_to_communities,
)
from graphify.community_index import CommunityIndex
from graphify.community_retrieval import (
    community_aware_expand,
    prepare_community_scope,
    restrict_to_communities,
    score_communities,
    select_communities,
)
from graphify.serve import _query_graph_text
from graphify.weighted_retrieval import expand_neighborhood


def _two_module_graph() -> nx.Graph:
    """Two densely connected modules linked by a weak bridge."""
    G = nx.Graph()
    auth = [("auth_login", "login"), ("auth_token", "token"), ("auth_user", "user")]
    db = [("db_connect", "connect"), ("db_query", "query"), ("db_pool", "pool")]
    for nid, label in auth + db:
        G.add_node(nid, label=label, source_file=f"{nid}.py")
    for a, b in (
        ("auth_login", "auth_token"),
        ("auth_token", "auth_user"),
        ("auth_login", "auth_user"),
        ("db_connect", "db_query"),
        ("db_query", "db_pool"),
        ("db_connect", "db_pool"),
    ):
        G.add_edge(a, b, relation="calls", confidence="EXTRACTED")
    G.add_edge("auth_user", "db_connect", relation="imports", confidence="EXTRACTED")
    for nid, _ in auth:
        G.nodes[nid]["community"] = 0
        G.nodes[nid]["community_name"] = "auth"
    for nid, _ in db:
        G.nodes[nid]["community"] = 1
        G.nodes[nid]["community_name"] = "database"
    return G


def _assign_detected_communities(G: nx.Graph) -> CommunityIndex:
    partition = detect_communities(G, algorithm="louvain")
    return CommunityIndex.from_partition(G, partition)


# --- community detection ----------------------------------------------------


def test_empty_graph_detection():
    G = nx.Graph()
    assert detect_communities(G, algorithm="louvain") == {}
    assert detect_communities(G, algorithm="label_propagation") == {}
    assert CommunityIndex.build(G).is_empty


def test_small_graph_detection():
    G = nx.Graph()
    G.add_nodes_from(["a", "b"])
    G.add_edge("a", "b")
    partition = detect_communities(G, algorithm="louvain")
    assert set(partition) == {"a", "b"}
    assert len(set(partition.values())) >= 1


def test_edgeless_graph_gets_singletons():
    G = nx.Graph()
    G.add_nodes_from(["x", "y", "z"])
    partition = detect_communities(G, algorithm="louvain")
    assert partition == {"x": 0, "y": 1, "z": 2}


def test_disconnected_graph_detection():
    G = nx.Graph()
    G.add_edges_from([("a1", "a2"), ("a2", "a3"), ("b1", "b2"), ("b2", "b3")])
    partition = detect_communities(G, algorithm="louvain")
    assert set(partition) == {"a1", "a2", "a3", "b1", "b2", "b3"}
    communities = partition_to_communities(partition)
    assert len(communities) >= 2


def test_detector_factory_and_names():
    for name in ("auto", "leiden", "louvain", "label_propagation"):
        detector = get_community_detector(name)
        assert detector.name == name
    with pytest.raises(ValueError):
        get_community_detector("not-a-real-algorithm")


def test_label_propagation_on_two_cliques():
    G = nx.Graph()
    G.add_edges_from([(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5)])
    detector = LabelPropagationCommunityDetector()
    partition = detector.detect(G)
    assert len(set(partition.values())) >= 2


def test_auto_detector_runs():
    G = _two_module_graph()
    partition = AutoCommunityDetector().detect(G)
    assert len(partition) == G.number_of_nodes()


# --- community assignment / index -------------------------------------------


def test_index_from_graph_attributes():
    G = _two_module_graph()
    index = CommunityIndex.from_graph_attributes(G)
    assert index is not None
    assert index.community_count == 2
    assert index.community_of("auth_login") == 0
    assert index.community_of("db_query") == 1
    auth = index.record(0)
    assert auth is not None
    assert auth.size == 3
    assert auth.name == "auth"
    assert len(auth.representatives) <= 3


def test_index_build_prefers_stored_over_detection():
    G = _two_module_graph()
    index = CommunityIndex.build(G, prefer_stored=True)
    assert index.community_of("auth_token") == 0
    assert index.community_of("db_pool") == 1


def test_index_build_detects_when_missing_attrs():
    G = nx.Graph()
    G.add_edges_from(
        [
            ("a1", "a2"),
            ("a2", "a3"),
            ("a1", "a3"),
            ("b1", "b2"),
            ("b2", "b3"),
            ("b1", "b3"),
        ]
    )
    index = CommunityIndex.build(G, prefer_stored=True, algorithm="louvain")
    assert not index.is_empty
    assert index.community_count >= 2


def test_from_partition_empty():
    G = nx.Graph()
    index = CommunityIndex.from_partition(G, {})
    assert index.is_empty


# --- community retrieval ----------------------------------------------------


def test_score_and_select_prefers_seed_community():
    G = _two_module_graph()
    index = CommunityIndex.from_graph_attributes(G)
    assert index is not None
    scores = score_communities(index, G, seeds=["auth_login"], query_terms=["auth"])
    assert scores[0] > scores[1]
    selection = select_communities(
        index,
        G,
        seeds=["auth_login"],
        query_terms=["login", "token"],
        min_confidence=0.5,
        max_communities=1,
    )
    assert selection.selected
    assert selection.community_ids == (0,)


def test_restrict_excludes_other_community_nodes():
    G = _two_module_graph()
    index = CommunityIndex.from_graph_attributes(G)
    assert index is not None
    scoped = restrict_to_communities(G, index, (0,), always_include=["auth_login"])
    assert set(scoped.nodes()) == {"auth_login", "auth_token", "auth_user"}
    assert "db_query" not in scoped


def test_community_aware_expand_narrows_search_space():
    G = _two_module_graph()
    index = CommunityIndex.from_graph_attributes(G)
    result, selection, scoped = community_aware_expand(
        G,
        ["auth_login"],
        depth=2,
        weighted=True,
        query_terms=["auth", "login"],
        index=index,
        community_aware=True,
        min_confidence=0.5,
        max_communities=1,
    )
    assert selection.selected
    assert "db_pool" not in result.nodes
    assert "auth_token" in result.nodes
    assert set(scoped.nodes()).isdisjoint({"db_connect", "db_query", "db_pool"})


def test_low_confidence_falls_back_to_full_graph():
    G = _two_module_graph()
    index = CommunityIndex.from_graph_attributes(G)
    # Seeds in both communities → low confidence / fallback.
    result, selection, scoped = community_aware_expand(
        G,
        ["auth_login", "db_query"],
        depth=2,
        weighted=True,
        query_terms=["login", "query"],
        index=index,
        community_aware=True,
        min_confidence=0.99,
        max_communities=1,
    )
    assert selection.used_fallback
    assert scoped.number_of_nodes() == G.number_of_nodes()
    assert "auth_token" in result.nodes or "db_pool" in result.nodes


def test_disabled_community_aware_matches_weighted_baseline():
    G = _two_module_graph()
    seeds = ["auth_login"]
    baseline = expand_neighborhood(G, seeds, 2, mode="bfs", weighted=True)
    result, selection, scoped = community_aware_expand(
        G,
        seeds,
        2,
        mode="bfs",
        weighted=True,
        community_aware=False,
    )
    assert selection.reason == "disabled"
    assert result.nodes == baseline.nodes
    assert scoped.number_of_nodes() == G.number_of_nodes()


def test_prepare_scope_empty_index_falls_back():
    G = nx.Graph()
    G.add_edge("a", "b")
    empty = CommunityIndex(by_id={}, node_to_community={})
    scoped, selection, index = prepare_community_scope(
        G,
        ["a"],
        index=empty,
    )
    assert selection.used_fallback
    assert selection.reason == "no_communities"
    assert scoped.number_of_nodes() == G.number_of_nodes()
    assert index.is_empty


def test_query_graph_text_community_aware_header():
    G = _two_module_graph()
    text = _query_graph_text(
        G,
        "auth login",
        depth=2,
        weighted=True,
        community_aware=True,
        community_min_confidence=0.5,
        max_communities=1,
    )
    assert "Communities=" in text or "Community fallback" in text
    assert "auth" in text.lower() or "login" in text.lower()


def test_query_graph_text_default_unchanged_from_weighted_path():
    G = _two_module_graph()
    default_text = _query_graph_text(G, "auth login", depth=2, weighted=True)
    explicit = _query_graph_text(
        G,
        "auth login",
        depth=2,
        weighted=True,
        community_aware=False,
    )
    assert default_text == explicit
    assert "Communities=" not in default_text


def test_regression_weighted_still_prefers_calls_inside_community():
    G = nx.DiGraph()
    for nid in ("seed", "via_call", "via_import", "noise"):
        G.add_node(nid, label=nid, community=0, community_name="mod")
    G.add_node("other", label="other", community=1, community_name="elsewhere")
    G.add_node("other_b", label="other_b", community=1, community_name="elsewhere")
    G.add_edge("seed", "via_call", relation="calls", confidence="EXTRACTED")
    G.add_edge("seed", "via_import", relation="imports", confidence="EXTRACTED")
    G.add_edge("via_import", "noise", relation="imports", confidence="EXTRACTED")
    G.add_edge("other", "other_b", relation="calls", confidence="EXTRACTED")

    result, selection, _scoped = community_aware_expand(
        G,
        ["seed"],
        depth=1,
        mode="bfs",
        weighted=True,
        query_terms=["seed"],
        community_aware=True,
        min_confidence=0.5,
        max_communities=1,
    )
    assert selection.selected or selection.reason == "single_community"
    # Within the community, weighted BFS still surfaces the calls neighbor.
    assert "via_call" in result.nodes


def test_louvain_detector_instance():
    G = _two_module_graph().copy()
    for _, data in G.nodes(data=True):
        data.pop("community", None)
        data.pop("community_name", None)
    detector = LouvainCommunityDetector()
    partition = detector.detect(G)
    index = CommunityIndex.from_partition(G, partition)
    assert not index.is_empty
