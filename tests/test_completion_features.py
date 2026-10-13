"""Acceptance tests for the five completion features and their interactions."""

from __future__ import annotations

import json

import networkx as nx

from graphify.community_retrieval import community_aware_expand
from graphify.lang_structural import extract_structural_references, validation_level
from graphify.learned_weights import (
    learn_from_feedback,
    learn_from_log,
    load_learned_registry,
    posterior_importance,
    registry_from_state,
)
from graphify.multigraph_store import ensure_multigraph, graph_from_node_link, graph_to_node_link, relation_edges
from graphify.pagerank_retrieval import personalized_pagerank, rank_by_pagerank
from graphify.preexec_validate import validate_code
from graphify.semantic_graph import apply_semantic_relations
from graphify.semantic_relations import SemanticExtractionResult, SemanticRelation
from graphify.serve import _query_graph_text
from graphify.weights import DEFAULT_RELATION_IMPORTANCE, RelationWeightRegistry
from graphify.weighted_retrieval import find_shortest_path


def _code_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("auth", label="auth")
    G.add_node("login", label="login")
    G.add_node("helper", label="helper")
    G.add_node("types", label="types")
    G.add_edge("auth", "login", relation="calls", confidence="EXTRACTED")
    G.add_edge("login", "helper", relation="calls", confidence="EXTRACTED")
    G.add_edge("auth", "types", relation="imports", confidence="EXTRACTED")
    return G


def test_feedback_moves_weight_and_ignores_benchmark_labels(tmp_path):
    prior = DEFAULT_RELATION_IMPORTANCE["imports"]
    records = [{"useful": True, "relations": ["imports"], "source": "user"} for _ in range(6)]
    records.append(
        {
            "useful": True,
            "relations": ["imports"],
            "source": "benchmark",
            "held_out": True,
            "question": "labelled retrieval question",
        }
    )
    state = learn_from_feedback(records, min_samples=3)
    assert state["records_used"] == 6
    assert state["records_excluded"] == 1
    learned = state["relations"]["imports"]["importance"]
    assert learned > prior
    registry = registry_from_state(state)
    assert registry.importance("imports") == learned
    assert registry.importance("calls") == DEFAULT_RELATION_IMPORTANCE["calls"]

    sparse = learn_from_feedback([{"useful": False, "relations": ["calls"]}], min_samples=3)
    assert registry_from_state(sparse).importance("calls") == DEFAULT_RELATION_IMPORTANCE["calls"]

    log = tmp_path / "feedback.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    dest = tmp_path / "learned_weights.json"
    learn_from_log(log, dest, min_samples=3)
    loaded = load_learned_registry(dest)
    assert loaded is not None
    assert loaded.importance("imports") == learned
    assert posterior_importance("imports", 6, 6) == learned


def test_fixed_weights_remain_when_learned_file_is_missing(tmp_path):
    assert load_learned_registry(tmp_path / "missing.json") is None


def test_learned_weights_change_path_cost_and_query_header():
    G = _code_graph()
    registry = RelationWeightRegistry()
    registry.register("imports", 1.0)
    registry.register("calls", 0.05)
    weighted, _ = find_shortest_path(G, "auth", "helper", weighted=True, registry=registry)
    assert weighted is not None
    text = _query_graph_text(G, "auth", registry=registry, learned=True)
    assert "Learned weights" in text
    assert "No matching nodes found." not in text


def test_parallel_semantic_edge_survives_round_trip_and_retrieval():
    G = ensure_multigraph(_code_graph())
    result = SemanticExtractionResult(
        relations=[SemanticRelation(source="auth", relation="handles", target="login", confidence="INFERRED")]
    )
    report = apply_semantic_relations(G, result)
    assert report.edges_augmented == 1
    relations = {edge["relation"] for edge in relation_edges(G, "auth", "login")}
    assert relations == {"calls", "handles"}
    restored = graph_from_node_link(graph_to_node_link(G))
    assert restored.is_multigraph()
    restored_relations = {edge["relation"] for edge in relation_edges(restored, "auth", "login")}
    assert restored_relations == {"calls", "handles"}
    path, _ = find_shortest_path(restored, "auth", "login", weighted=True)
    assert path == ["auth", "login"]


def test_secondary_relations_migrate_to_parallel_edges():
    G = nx.DiGraph()
    G.add_node("a", label="a")
    G.add_node("b", label="b")
    G.add_edge(
        "a",
        "b",
        relation="imports",
        secondary_relations=[{"relation": "validates", "_origin": "semantic_code", "confidence": "INFERRED"}],
    )
    multi = ensure_multigraph(G)
    relations = {edge["relation"] for edge in relation_edges(multi, "a", "b")}
    assert relations == {"imports", "validates"}
    assert "secondary_relations" not in relation_edges(multi, "a", "b")[0]


def test_javascript_tree_sitter_distinguishes_unknown_from_syntax_errors():
    assert validation_level("javascript") == "tree-sitter"
    assert validation_level("python") == "ast"
    assert validation_level("not-a-language") == "unsupported"
    extracted = extract_structural_references(
        "import auth from './auth';\nauth.login();\n",
        "javascript",
    )
    assert extracted.syntax_error is False
    assert "login" in extracted.simple_calls or ("auth", "login") in extracted.member_calls
    broken = extract_structural_references("function (", "javascript")
    assert broken.syntax_error is True
    assert any("syntax error" in note for note in broken.notes)

    G = _code_graph()
    report = validate_code(G, "auth.login();\n", file_path="app.js")
    assert report.blocked is False
    assert report.action.language == "javascript"
    unknown = validate_code(G, "missing.nope();\n", file_path="app.js")
    assert unknown.verdict.value != "CONTRADICTED"


def test_personalized_pagerank_is_deterministic_and_prefers_call_chain():
    G = _code_graph()
    first = personalized_pagerank(G, ["auth"])
    second = personalized_pagerank(G, ["auth"])
    assert first == second
    ranked = rank_by_pagerank(first)
    assert ranked[0] == "auth"
    assert ranked.index("login") < ranked.index("types")
    assert personalized_pagerank(G, []) == {}
    dangling = nx.DiGraph()
    dangling.add_node("only", label="only")
    scores = personalized_pagerank(dangling, ["only"])
    assert scores["only"] == 1.0
    text = _query_graph_text(G, "auth", retrieval="ppr")
    assert "Personalized PageRank" in text


def test_ppr_uses_learned_registry_and_multigraph_edges():
    G = ensure_multigraph(_code_graph())
    apply_semantic_relations(
        G,
        SemanticExtractionResult(
            relations=[SemanticRelation(source="auth", relation="handles", target="types", confidence="INFERRED")]
        ),
    )
    registry = RelationWeightRegistry()
    registry.register("handles", 1.0)
    registry.register("imports", 0.05)
    registry.register("calls", 0.05)
    scores = personalized_pagerank(G, ["auth"], registry=registry)
    assert scores["types"] > 0
    text = _query_graph_text(G, "auth login", retrieval="ppr", registry=registry, learned=True, community_aware=True)
    assert "Personalized PageRank" in text
    assert "Learned weights" in text
    community_aware_expand(G, ["auth"], 2, community_aware=True)


def test_unsupported_language_does_not_invent_a_hallucination():
    report = validate_code(nx.DiGraph(), "whatever", file_path="notes.xyz")
    assert report.blocked is False
    assert report.checks.results == []
    assert any("unsupported" in note or "validation level" in note for note in report.notes)
