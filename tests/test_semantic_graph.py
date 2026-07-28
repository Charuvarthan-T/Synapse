"""Tests for semantic code graph enhancement."""

from __future__ import annotations

import networkx as nx
import pytest

from graphify.semantic_extraction import (
    BackendLLMProvider,
    CodeUnit,
    RetryingProvider,
    SemanticExtractionError,
    batch_code_units,
    build_semantic_prompt,
    default_provider,
    extract_semantic_relations,
    select_code_units,
)
from graphify.semantic_graph import (
    SECONDARY_RELATIONS_KEY,
    apply_semantic_relations,
)
from graphify.semantic_relations import (
    SEMANTIC_RELATION_TYPES,
    SemanticExtractionResult,
    SemanticRelation,
    parse_semantic_relations,
)
from graphify.weighted_retrieval import expand_neighborhood
from graphify.weights import get_default_registry


def _code_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("jwt_validator", label="JWTValidator", file_type="code", source_file="auth.py")
    G.add_node("session_store", label="SessionStore", file_type="code", source_file="session.py")
    G.add_node("login_handler", label="LoginHandler", file_type="code", source_file="auth.py")
    G.add_edge(
        "login_handler",
        "jwt_validator",
        relation="calls",
        confidence="EXTRACTED",
        source_file="auth.py",
    )
    return G


class _FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        self.calls += 1
        return self._responses.pop(0)


class _AlwaysFailsProvider:
    def complete(self, prompt: str, *, max_tokens: int) -> str:
        raise RuntimeError("network unavailable")


# --- semantic extraction parsing --------------------------------------------


def test_parse_valid_relations_resolves_known_ids():
    raw = (
        '{"relations": [{"source": "jwt_validator", "relation": "validates", '
        '"target": "session_store", "confidence": "EXTRACTED", "rationale": "checks session"}]}'
    )
    known = frozenset({"jwt_validator", "session_store"})
    result = parse_semantic_relations(raw, known_node_ids=known)
    assert not result.errors
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.source == "jwt_validator"
    assert rel.relation == "validates"
    assert rel.target == "session_store"
    assert rel.confidence == "EXTRACTED"
    assert not result.concept_nodes


def test_parse_unmatched_target_becomes_concept_node():
    raw = '{"relations": [{"source": "jwt_validator", "relation": "validates", "target": "user_session"}]}'
    result = parse_semantic_relations(raw, known_node_ids=frozenset({"jwt_validator"}))
    assert len(result.relations) == 1
    assert result.relations[0].target.startswith("concept_")
    assert len(result.concept_nodes) == 1
    assert result.concept_nodes[0]["file_type"] == "concept"
    assert result.relations[0].confidence == "INFERRED"  # default when omitted


def test_parse_rejects_unsupported_relation_type():
    raw = '{"relations": [{"source": "a", "relation": "destroys", "target": "b"}]}'
    result = parse_semantic_relations(raw, known_node_ids=frozenset({"a", "b"}))
    assert not result.relations
    assert any("unsupported type" in e for e in result.errors)


def test_parse_rejects_self_loop():
    raw = '{"relations": [{"source": "a", "relation": "manages", "target": "a"}]}'
    result = parse_semantic_relations(raw, known_node_ids=frozenset({"a"}))
    assert not result.relations
    assert any("self-loop" in e for e in result.errors)


def test_parse_rejects_missing_fields():
    raw = '{"relations": [{"relation": "handles", "target": "b"}, {"source": "a", "relation": "handles"}]}'
    result = parse_semantic_relations(raw, known_node_ids=frozenset({"a", "b"}))
    assert not result.relations
    assert len(result.errors) == 2


# --- invalid LLM responses ---------------------------------------------------


def test_invalid_json_yields_empty_result_not_exception():
    result = parse_semantic_relations("not json at all", known_node_ids=frozenset())
    assert result.is_empty
    assert result.errors


def test_missing_relations_key_recorded_as_error():
    result = parse_semantic_relations('{"nodes": [], "edges": []}', known_node_ids=frozenset())
    assert result.is_empty
    assert "response missing a 'relations' list" in result.errors


def test_non_list_relations_recorded_as_error():
    result = parse_semantic_relations('{"relations": "oops"}', known_node_ids=frozenset())
    assert result.is_empty
    assert result.errors


def test_non_dict_relation_entry_skipped():
    raw = '{"relations": [["not", "a", "dict"], {"source": "a", "relation": "handles", "target": "b"}]}'
    result = parse_semantic_relations(raw, known_node_ids=frozenset({"a", "b"}))
    assert len(result.relations) == 1
    assert any("not an object" in e for e in result.errors)


# --- code unit selection / batching / prompts -------------------------------


def test_select_code_units_filters_by_file_type():
    G = _code_graph()
    G.add_node("readme", label="README", file_type="document", source_file="README.md")
    units = select_code_units(G)
    assert {u.node_id for u in units} == {"jwt_validator", "session_store", "login_handler"}


def test_select_code_units_deterministic_order():
    G = _code_graph()
    first = select_code_units(G)
    second = select_code_units(G)
    assert [u.node_id for u in first] == [u.node_id for u in second]


def test_batch_code_units_respects_size():
    units = [CodeUnit(node_id=str(i), label=str(i), source_file="f.py") for i in range(5)]
    batches = batch_code_units(units, batch_size=2)
    assert [len(b) for b in batches] == [2, 2, 1]


def test_batch_code_units_rejects_non_positive_size():
    with pytest.raises(ValueError):
        batch_code_units([], batch_size=0)


def test_build_semantic_prompt_lists_allowed_relations():
    units = [CodeUnit(node_id="a", label="Alpha", source_file="a.py", source_location="L1")]
    prompt = build_semantic_prompt(units)
    for relation in SEMANTIC_RELATION_TYPES:
        assert relation in prompt
    assert "a: Alpha @ a.py:L1" in prompt


# --- provider abstraction / retry -------------------------------------------


def test_retrying_provider_succeeds_after_transient_failures():
    calls = {"n": 0}

    class _FlakyProvider:
        def complete(self, prompt: str, *, max_tokens: int) -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("temporary")
            return '{"relations": []}'

    provider = RetryingProvider(
        _FlakyProvider(), max_attempts=3, backoff_seconds=0, sleep=lambda _: None
    )
    assert provider.complete("prompt", max_tokens=100) == '{"relations": []}'
    assert calls["n"] == 2


def test_retrying_provider_raises_after_exhausting_attempts():
    provider = RetryingProvider(
        _AlwaysFailsProvider(), max_attempts=2, backoff_seconds=0, sleep=lambda _: None
    )
    with pytest.raises(SemanticExtractionError):
        provider.complete("prompt", max_tokens=100)


def test_default_provider_returns_none_without_backend(monkeypatch):
    monkeypatch.setattr("graphify.llm.detect_backend", lambda: None)
    assert default_provider() is None


def test_backend_llm_provider_delegates_to_call_llm(monkeypatch):
    captured = {}

    def _fake_call_llm(prompt, *, backend, max_tokens, model=None):
        captured.update(prompt=prompt, backend=backend, max_tokens=max_tokens, model=model)
        return "ok"

    monkeypatch.setattr("graphify.llm._call_llm", _fake_call_llm)
    provider = BackendLLMProvider(backend="gemini", model="test-model")
    assert provider.complete("hi", max_tokens=50) == "ok"
    assert captured == {
        "prompt": "hi",
        "backend": "gemini",
        "max_tokens": 50,
        "model": "test-model",
    }


# --- extraction orchestration / missing semantic data -----------------------


def test_extract_semantic_relations_with_no_provider_is_empty(monkeypatch):
    monkeypatch.setattr("graphify.semantic_extraction.default_provider", lambda **_: None)
    G = _code_graph()
    result = extract_semantic_relations(G, provider=None)
    assert result.is_empty
    assert "no semantic LLM provider configured" in result.errors


def test_extract_semantic_relations_with_no_code_units():
    G = nx.Graph()
    G.add_node("doc", label="Doc", file_type="document", source_file="README.md")
    provider = _FakeProvider(['{"relations": []}'])
    result = extract_semantic_relations(G, provider=provider)
    assert result.is_empty
    assert "no code units found for semantic extraction" in result.errors
    assert provider.calls == 0


def test_extract_semantic_relations_aggregates_batches():
    G = _code_graph()
    provider = _FakeProvider(
        [
            '{"relations": [{"source": "login_handler", "relation": "handles", "target": "jwt_validator"}]}',
        ]
    )
    result = extract_semantic_relations(G, provider=provider, batch_size=20)
    assert len(result.relations) == 1
    assert provider.calls == 1


def test_extract_semantic_relations_batch_failure_is_non_fatal():
    G = _code_graph()
    failing = RetryingProvider(_AlwaysFailsProvider(), max_attempts=1, sleep=lambda _: None)
    result = extract_semantic_relations(G, provider=failing)
    assert result.is_empty
    assert result.errors


# --- graph insertion ---------------------------------------------------------


def test_apply_semantic_relations_adds_new_edge_between_unconnected_nodes():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="jwt_validator", relation="validates", target="session_store")
        ]
    )
    report = apply_semantic_relations(G, result)
    assert report.edges_added == 1
    assert report.edges_augmented == 0
    assert G.has_edge("jwt_validator", "session_store")
    assert G["jwt_validator"]["session_store"]["relation"] == "validates"


def test_apply_semantic_relations_preserves_existing_structural_edge():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="login_handler", relation="handles", target="jwt_validator")
        ]
    )
    report = apply_semantic_relations(G, result)
    assert report.edges_added == 0
    assert report.edges_augmented == 1
    edge = G["login_handler"]["jwt_validator"]
    assert edge["relation"] == "calls"  # untouched
    assert edge["confidence"] == "EXTRACTED"
    secondary = edge[SECONDARY_RELATIONS_KEY]
    assert secondary[0]["relation"] == "handles"


def test_apply_semantic_relations_creates_concept_nodes():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(
                source="jwt_validator", relation="validates", target="concept_user_session"
            )
        ],
        concept_nodes=[
            {
                "id": "concept_user_session",
                "label": "user_session",
                "file_type": "concept",
                "source_file": "",
            }
        ],
    )
    report = apply_semantic_relations(G, result)
    assert report.concept_nodes_added == 1
    assert G.nodes["concept_user_session"]["file_type"] == "concept"
    assert G.has_edge("jwt_validator", "concept_user_session")


def test_apply_semantic_relations_idempotent_on_repeated_runs():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="login_handler", relation="handles", target="jwt_validator")
        ]
    )
    apply_semantic_relations(G, result)
    second_report = apply_semantic_relations(G, result)
    assert second_report.edges_augmented == 0
    assert second_report.relations_skipped == 1
    assert len(G["login_handler"]["jwt_validator"][SECONDARY_RELATIONS_KEY]) == 1


def test_apply_semantic_relations_skips_dangling_endpoints():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="jwt_validator", relation="manages", target="ghost_node")
        ]
    )
    report = apply_semantic_relations(G, result)
    assert report.edges_added == 0
    assert report.relations_skipped == 1


def test_apply_semantic_relations_on_empty_result_is_a_no_op():
    G = _code_graph()
    before_nodes = G.number_of_nodes()
    before_edges = G.number_of_edges()
    report = apply_semantic_relations(G, SemanticExtractionResult())
    assert report.edges_added == 0
    assert report.concept_nodes_added == 0
    assert G.number_of_nodes() == before_nodes
    assert G.number_of_edges() == before_edges


# --- compatibility with existing (weighted) retrieval ------------------------


def test_semantic_edge_is_traversable_by_weighted_retrieval():
    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="jwt_validator", relation="validates", target="session_store")
        ]
    )
    apply_semantic_relations(G, result)
    traversal = expand_neighborhood(G, ["login_handler"], depth=2, weighted=True)
    assert "session_store" in traversal.nodes


def test_secondary_relation_importance_falls_back_gracefully():
    G = _code_graph()
    registry = get_default_registry()
    baseline = registry.edge_importance(G["login_handler"]["jwt_validator"])
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="login_handler", relation="handles", target="jwt_validator")
        ]
    )
    apply_semantic_relations(G, result)
    augmented = registry.edge_importance(G["login_handler"]["jwt_validator"])
    assert augmented >= baseline


def test_edge_importance_unaffected_when_no_secondary_relations():
    registry = get_default_registry()
    data = {"relation": "calls", "confidence": "EXTRACTED"}
    assert registry.edge_importance(data) == registry.edge_importance(dict(data))


def test_registered_semantic_relation_importances():
    registry = get_default_registry()
    assert registry.importance("handles") > registry.importance("related_to")
    assert registry.importance("manages") < registry.importance("calls")


def test_semantic_edges_compatible_with_community_retrieval():
    from graphify.community_index import CommunityIndex
    from graphify.community_retrieval import community_aware_expand

    G = _code_graph()
    result = SemanticExtractionResult(
        relations=[
            SemanticRelation(source="jwt_validator", relation="validates", target="session_store")
        ]
    )
    apply_semantic_relations(G, result)
    index = CommunityIndex.build(G, algorithm="louvain")
    assert not index.is_empty
    traversal, selection, _scoped = community_aware_expand(
        G,
        ["login_handler"],
        depth=2,
        weighted=True,
        query_terms=["login"],
        index=index,
        community_aware=True,
        min_confidence=0.0,
    )
    assert "jwt_validator" in traversal.nodes
    assert selection is not None
