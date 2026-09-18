"""Unit + integration tests for Synapse C2 (bidirectional reasoning)."""

from __future__ import annotations

import json

import networkx as nx

from graphify.bidirectional_reasoner import (
    format_final_answer,
    reason,
)
from graphify.claims import Claim, parse_claims, validate_claim, validate_claims
from graphify.graph_checks import Verdict, check_calls, check_exists, check_inherits


def _auth_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("login", label="LoginHandler", file_type="code", source_file="auth.py")
    G.add_node("session", label="SessionStore", file_type="code", source_file="session.py")
    G.add_node("base", label="BaseHandler", file_type="code", source_file="base.py")
    G.add_node("utils", label="Utils", file_type="code", source_file="utils.py")
    G.add_edge("login", "session", relation="calls", confidence="EXTRACTED")
    G.add_edge("login", "base", relation="inherits", confidence="EXTRACTED")
    G.add_edge("login", "utils", relation="imports", confidence="EXTRACTED")
    return G


class _ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        return self._responses.pop(0)


def test_check_exists_supported_and_unknown():
    G = _auth_graph()
    ok = check_exists(G, "LoginHandler")
    assert ok.verdict is Verdict.SUPPORTED
    missing = check_exists(G, "PaymentManager")
    assert missing.verdict is Verdict.UNKNOWN
    assert missing.status == "UNKNOWN"


def test_check_calls_supported_and_unknown():
    G = _auth_graph()
    assert check_calls(G, "LoginHandler", "SessionStore").verdict is Verdict.SUPPORTED
    # Both exist but no edge → UNKNOWN (not contradicted)
    assert check_calls(G, "SessionStore", "LoginHandler").verdict is Verdict.UNKNOWN


def test_check_inherits_contradicted_when_other_parent():
    G = _auth_graph()
    result = check_inherits(G, "LoginHandler", "SessionStore")
    assert result.verdict is Verdict.CONTRADICTED
    assert result.status == "INVALID"


def test_parse_and_validate_claims_bundle():
    G = _auth_graph()
    raw = json.dumps(
        {
            "claims": [
                {"type": "exists", "symbol": "LoginHandler"},
                {"type": "calls", "source": "LoginHandler", "target": "SessionStore"},
                {"type": "calls", "source": "LoginHandler", "target": "MissingFn"},
                # SessionStore exists but is not the parent → CONTRADICTED
                {"type": "inherits", "source": "LoginHandler", "target": "SessionStore"},
            ]
        }
    )
    claims, errors = parse_claims(raw)
    assert not errors
    assert len(claims) == 4
    report = validate_claims(G, claims)
    assert report.verdict is Verdict.CONTRADICTED
    assert report.to_dict()["counts"]["supported"] >= 2
    assert report.to_dict()["counts"]["contradicted"] >= 1
    assert report.to_dict()["counts"]["unknown"] >= 1


def test_bidirectional_reason_revises_on_contradiction():
    G = _auth_graph()
    draft = json.dumps(
        {
            "answer": "LoginHandler inherits SessionStore and calls it.",
            "claims": [
                {"type": "inherits", "source": "LoginHandler", "target": "SessionStore"},
                {"type": "calls", "source": "LoginHandler", "target": "SessionStore"},
            ],
        }
    )
    revised = json.dumps(
        {
            "answer": "LoginHandler calls SessionStore; it inherits BaseHandler, not SessionStore.",
            "claims": [
                {"type": "calls", "source": "LoginHandler", "target": "SessionStore"},
                {"type": "inherits", "source": "LoginHandler", "target": "BaseHandler"},
            ],
        }
    )
    provider = _ScriptedProvider([draft, revised])
    result = reason(
        G,
        "How does login relate to session?",
        provider=provider,
        revise=True,
        graph_context="NODE LoginHandler\nEDGE LoginHandler -[calls]→ SessionStore",
        community_aware=False,
    )
    assert provider.calls == 2
    assert result.revised is True
    assert "BaseHandler" in result.final_answer or "calls" in result.final_answer.lower()
    assert "Graph validation" in result.final_answer
    assert any(r.verdict is Verdict.SUPPORTED for r in result.validation.results)


def test_bidirectional_reason_explicit_none_provider_path():
    """When no LLM backend is configured, fail soft and keep graph context."""
    G = _auth_graph()
    import graphify.semantic_extraction as se

    original = se.default_provider
    se.default_provider = lambda **kwargs: None
    try:
        from graphify import bidirectional_reasoner as br

        result = br.reason(G, "q", provider=None, graph_context="GRAPH CTX")
    finally:
        se.default_provider = original
    assert "Cannot run bidirectional reasoning" in result.draft_answer
    assert result.provider_errors
    assert result.graph_context == "GRAPH CTX"


def test_reason_refuses_uncorrected_contradiction():
    G = _auth_graph()
    bad = json.dumps(
        {
            "answer": "LoginHandler inherits SessionStore.",
            "claims": [
                {"type": "inherits", "source": "LoginHandler", "target": "SessionStore"},
            ],
        }
    )
    # Second response still bad / empty claims — still contradicted.
    still_bad = json.dumps(
        {
            "answer": "LoginHandler inherits SessionStore.",
            "claims": [
                {"type": "inherits", "source": "LoginHandler", "target": "SessionStore"},
            ],
        }
    )
    provider = _ScriptedProvider([bad, still_bad])
    result = reason(
        G,
        "inheritance?",
        provider=provider,
        revise=True,
        graph_context="ctx",
    )
    assert "contradict" in result.final_answer.lower() or result.validation.verdict is Verdict.CONTRADICTED


def test_format_final_answer_includes_evidence():
    G = _auth_graph()
    claim = Claim(type="exists", symbol="LoginHandler", source="LoginHandler")
    report = validate_claims(G, [claim])
    from graphify.bidirectional_reasoner import ReasoningResult

    text = format_final_answer(
        ReasoningResult(
            question="q",
            graph_context="c",
            draft_answer="LoginHandler exists.",
            claims=[claim],
            validation=report,
        )
    )
    assert "[SUPPORTED]" in text
    assert "LoginHandler" in text


def test_validate_claim_method_of_helper():
    G = nx.DiGraph()
    G.add_node("cls", label="PaymentManager", file_type="code")
    G.add_node("m", label="charge", file_type="code")
    G.add_edge("cls", "m", relation="method")
    result = validate_claim(
        G, Claim(type="method_of", source="PaymentManager", target="charge")
    )
    assert result.verdict is Verdict.SUPPORTED
