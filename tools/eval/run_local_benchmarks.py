"""Local benchmarks that do not call a paid model.

Writes validation, multigraph, reasoning, learned-weight, and in-process
latency artifacts under tools/eval/results/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from graphify.graph_checks import Verdict, check_calls, check_exists
from graphify.lang_structural import validation_level
from graphify.learned_weights import learn_from_feedback, registry_from_state
from graphify.multigraph_store import ensure_multigraph, graph_from_node_link, graph_to_node_link, relation_edges
from graphify.pagerank_retrieval import personalized_pagerank
from graphify.preexec_validate import validate_code
from graphify.semantic_graph import apply_semantic_relations
from graphify.semantic_relations import SemanticExtractionResult, SemanticRelation
from graphify.serve import _query_graph_text
from graphify.weights import DEFAULT_RELATION_IMPORTANCE

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ROOT = HERE.parent.parent


def _sha() -> str:
    import subprocess

    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _stamp(name: str) -> dict:
    return {
        "benchmark": name,
        "benchmark_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synapse_commit": _sha(),
        "completed": True,
    }


def _graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("auth", label="auth")
    G.add_node("login", label="login")
    G.add_edge("auth", "login", relation="calls", confidence="EXTRACTED")
    return G


def validation() -> dict:
    G = _graph()
    cases = [
        ("py-valid-call", "python", "login()\n", "a.py", "not_contradicted"),
        ("py-bad-method", "python", "auth.delete_everything()\n", "a.py", "not_supported"),
        ("py-unknown", "python", "missing_symbol()\n", "a.py", "unknown"),
        ("js-call", "javascript", "auth.login();\n", "a.js", "not_contradicted"),
        ("js-syntax", "javascript", "function (\n", "a.js", "syntax_note"),
        ("js-unknown", "javascript", "missing.nope();\n", "a.js", "unknown"),
        ("unsupported", "unknown", "whatever\n", "notes.xyz", "no_checks"),
    ]
    rows = []
    for name, _lang, code, path, expect in cases:
        report = validate_code(G, code, file_path=path)
        verdict = report.verdict.value
        notes = " ".join(report.notes)
        if expect == "not_contradicted":
            ok = verdict != "CONTRADICTED"
        elif expect == "not_supported":
            ok = verdict != "SUPPORTED"
        elif expect == "unknown":
            ok = verdict != "CONTRADICTED"
        elif expect == "syntax_note":
            ok = "syntax error" in notes and verdict != "CONTRADICTED"
        else:
            ok = report.checks.results == [] and verdict != "CONTRADICTED"
        rows.append({"id": name, "expected": expect, "verdict": verdict, "ok": ok, "notes": notes[:240]})
    correct = sum(1 for row in rows if row["ok"])
    false_positives = sum(1 for row in rows if row["expected"] != "not_supported" and row["verdict"] == "CONTRADICTED")
    payload = _stamp("code_validation")
    payload.update({
        "n": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "false_positives": false_positives,
        "levels": {lang: validation_level(lang) for lang in ("python", "javascript", "go", "not-a-language")},
        "original_graphify": "NO_EQUIVALENT_VALIDATOR",
        "tasks": rows,
    })
    return payload


def representation() -> dict:
    G = ensure_multigraph(_graph())
    apply_semantic_relations(
        G,
        SemanticExtractionResult(relations=[SemanticRelation("auth", "handles", "login", "INFERRED")]),
    )
    relations = sorted({edge["relation"] for edge in relation_edges(G, "auth", "login")})
    restored = graph_from_node_link(graph_to_node_link(G))
    again = sorted({edge["relation"] for edge in relation_edges(restored, "auth", "login")})
    payload = _stamp("graph_representation")
    payload.update({
        "parallel_relations": relations,
        "reloaded_relations": again,
        "round_trip_ok": relations == again == ["calls", "handles"],
        "original_graphify": "SIMPLE_GRAPH_COLLAPSES_PARALLEL_EDGES",
    })
    return payload


def reasoning() -> dict:
    G = _graph()
    cases = [
        ("supported-exists", check_exists(G, "auth").verdict, Verdict.SUPPORTED),
        ("unknown-exists", check_exists(G, "missing").verdict, Verdict.UNKNOWN),
        ("supported-calls", check_calls(G, "auth", "login").verdict, Verdict.SUPPORTED),
        ("unknown-calls", check_calls(G, "auth", "missing").verdict, Verdict.UNKNOWN),
    ]
    rows = [{"id": name, "actual": actual.value, "expected": expected.value, "ok": actual is expected} for name, actual, expected in cases]
    payload = _stamp("graph_evidence_checks")
    payload.update({
        "n": len(rows),
        "correct": sum(1 for row in rows if row["ok"]),
        "note": "Deterministic graph checks. The LLM revision loop is covered by tests/test_bidirectional_reasoner.py with a fake provider, not a live model.",
        "tasks": rows,
    })
    return payload


def learned() -> dict:
    prior = DEFAULT_RELATION_IMPORTANCE["imports"]
    dev = [{"useful": True, "relations": ["imports"], "source": "dev-feedback"} for _ in range(6)]
    dev.append({"useful": True, "relations": ["imports"], "source": "benchmark", "held_out": True})
    state = learn_from_feedback(dev, min_samples=3)
    registry = registry_from_state(state)
    G = _graph()
    G.add_node("types", label="types")
    G.add_edge("auth", "types", relation="imports", confidence="EXTRACTED")
    fixed = _query_graph_text(G, "auth", learned=False)
    learned_text = _query_graph_text(G, "auth", registry=registry, learned=True)
    payload = _stamp("learned_weights")
    payload.update({
        "prior_imports": prior,
        "learned_imports": state["relations"]["imports"]["importance"],
        "records_used": state["records_used"],
        "records_excluded": state["records_excluded"],
        "held_out_benchmark_record_excluded": state["records_excluded"] == 1,
        "retrieval_uses_learned_header": "Learned weights" in learned_text and "Learned weights" not in fixed,
        "note": "Development feedback is synthetic and is not the 20-question ground truth. This shows the update rule, not a user-study gain.",
    })
    return payload


def performance() -> dict:
    G = _graph()
    for i in range(30):
        G.add_node(f"n{i}", label=f"n{i}")
        G.add_edge("auth", f"n{i}", relation="imports", confidence="EXTRACTED")
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        _query_graph_text(G, "auth")
        samples.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    personalized_pagerank(G, ["auth"])
    ppr = time.perf_counter() - t0
    payload = _stamp("in_process_latency")
    payload.update({
        "nodes": G.number_of_nodes(),
        "weighted_query_seconds": samples,
        "weighted_query_median_s": sorted(samples)[len(samples) // 2],
        "ppr_seconds": ppr,
        "note": "In-process timings. CLI latency in retrieval_summary.json includes process startup.",
    })
    return payload


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    files = {
        "validation_summary.json": validation(),
        "semantic_graph_results.json": representation(),
        "reasoning_results.json": reasoning(),
        "learned_weights_results.json": learned(),
        "performance_results.json": performance(),
    }
    for name, payload in files.items():
        (RESULTS / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(name, "ok" if payload.get("completed") else "fail")


if __name__ == "__main__":
    main()
