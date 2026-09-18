"""Unit + integration tests for Synapse C3 (pre-execution validation)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import networkx as nx

from graphify.graph_checks import Verdict
from graphify.preexec_validate import (
    ENV_PREEXEC,
    ProposedAction,
    extract_python_references,
    hook_decision_payload,
    proposal_from_tool_input,
    validate_code,
    validate_proposal,
)


def _payments_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("pm", label="PaymentManager", file_type="code", source_file="pay.py")
    G.add_node("charge", label="charge", file_type="code", source_file="pay.py")
    G.add_node("refund", label="process_refund", file_type="code", source_file="pay.py")
    G.add_node("gateway", label="payments.gateway", file_type="code", source_file="gateway.py")
    G.add_edge("pm", "charge", relation="method", confidence="EXTRACTED")
    G.add_edge("pm", "refund", relation="method", confidence="EXTRACTED")
    G.add_edge("pm", "gateway", relation="imports", confidence="EXTRACTED")
    G.add_edge("charge", "gateway", relation="calls", confidence="EXTRACTED")
    return G


def test_extract_imports_and_calls_via_ast():
    code = (
        "from payments.gateway import Client\n"
        "import os\n"
        "PaymentManager.charge()\n"
        "helper()\n"
    )
    refs = extract_python_references(code)
    assert ("from_import", "payments.gateway", "Client") in refs["imports"]
    assert ("import", "os") in refs["imports"]
    assert ("attr_call", "PaymentManager", "charge") in refs["attr_calls"]
    assert ("method_of", "PaymentManager", "charge") in refs["methods"]
    assert ("call", "helper") in refs["simple_calls"]


def test_validate_code_valid_method_call():
    G = _payments_graph()
    report = validate_code(G, "PaymentManager.charge()\n")
    assert report.status in {"VALID", "UNKNOWN"}  # calls edge may be UNKNOWN
    assert any(r.verdict is Verdict.SUPPORTED for r in report.checks.results)
    assert report.blocked is False


def test_validate_code_invalid_unknown_method_on_known_owner():
    G = _payments_graph()
    report = validate_code(G, "PaymentManager.missing_method()\n", strict=True)
    assert any(r.verdict is Verdict.CONTRADICTED for r in report.checks.results)
    assert report.status == "INVALID"
    assert report.blocked is True
    # Evidence should mention owner exists
    contradicted = next(r for r in report.checks.results if r.verdict is Verdict.CONTRADICTED)
    assert "PaymentManager" in contradicted.reason or contradicted.evidence


def test_validate_code_unknown_symbol_does_not_invalidate():
    G = _payments_graph()
    report = validate_code(G, "TotallyNewThing.run()\n", strict=True)
    assert report.status == "UNKNOWN"
    assert report.blocked is False
    assert all(r.verdict is not Verdict.CONTRADICTED for r in report.checks.results)


def test_proposal_from_write_tool_and_hook_payload():
    G = _payments_graph()
    action = proposal_from_tool_input(
        "Write",
        {"file_path": "pay.py", "content": "PaymentManager.process_refund()\n"},
    )
    assert action.kind == "write"
    report = validate_proposal(G, action, strict=False)
    assert report.blocked is False
    payload = hook_decision_payload(report)
    assert "hookSpecificOutput" in payload
    assert "Pre-execution" in json.dumps(payload)


def test_strict_hook_denies_on_invalid():
    G = _payments_graph()
    action = ProposedAction(
        kind="edit",
        content="PaymentManager.nope()\n",
        file_path="pay.py",
    )
    report = validate_proposal(G, action, strict=True)
    assert report.blocked is True
    payload = hook_decision_payload(report)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_preexec_disabled_by_default_in_env_helper():
    from graphify.preexec_validate import preexec_enabled

    with patch.dict(os.environ, {ENV_PREEXEC: ""}, clear=False):
        os.environ.pop(ENV_PREEXEC, None)
        assert preexec_enabled() is False
    with patch.dict(os.environ, {ENV_PREEXEC: "1"}):
        assert preexec_enabled() is True


def test_regex_fallback_on_incomplete_snippet():
    refs = extract_python_references("from x.y import z\nFoo.bar(")
    assert ("from_import", "x.y", "z") in refs["imports"]
    assert ("attr_call", "Foo", "bar") in refs["attr_calls"]


def test_import_existence_unknown_is_not_blocked():
    G = _payments_graph()
    report = validate_code(
        G,
        "from totally.missing import Thing\n",
        strict=True,
    )
    assert report.blocked is False
    assert report.status == "UNKNOWN"
