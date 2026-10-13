"""Compare upstream Graphify with Synapse on the frozen seven-file corpus.

Upstream is executed from a separate checkout via PYTHONPATH so this repo's
package is not used as the baseline. The labelled questions are not used to
train weights.

    python tools/eval/run_graphify_comparison.py
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RESULTS = HERE / "results"
GRAPH = HERE / "frozen_corpus" / "graphify-out" / "graph.json"
UPSTREAM = Path(os.environ.get("GRAPHIFY_UPSTREAM", Path(os.environ["TEMP"]) / "graphify-upstream"))
QUESTIONS = HERE / "ground_truth.json"

SYNAPSE_MODES = {
    "synapse_unweighted": ["--unweighted"],
    "synapse_weighted": [],
    "synapse_community": ["--community-aware"],
    "synapse_ppr": ["--ppr"],
    "synapse_learned": ["--learned-weights"],
    "synapse_full_flags": ["--ppr", "--learned-weights", "--community-aware"],
}


def _query(cmd: list[str], env: dict, cwd: Path) -> tuple[list[str], float, int]:
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    labels = []
    for line in proc.stdout.splitlines():
        if line.startswith("NODE "):
            labels.append(line.split("[", 1)[0].replace("NODE ", "", 1).strip())
    return labels, elapsed, proc.returncode


def _score(retrieved: list[str], relevant: list[str], label_to_id: dict[str, str], k: int) -> dict:
    relevant_set = set(relevant)
    ids = [label_to_id.get(label, label) for label in retrieved]
    top = ids[:k]
    hits = sum(1 for item in top if item in relevant_set)

    def at(n: int) -> dict:
        window = ids[:n]
        got = sum(1 for item in window if item in relevant_set)
        return {
            f"precision_at_{n}": (got / len(window) if window else 0.0),
            f"recall_at_{n}": (got / len(relevant_set) if relevant_set else 0.0),
        }

    rr = 0.0
    for rank, item in enumerate(ids, start=1):
        if item in relevant_set:
            rr = 1.0 / rank
            break
    row = {"hits_at_10": hits, "mrr_contribution": rr}
    for n in (1, 5, 10):
        row.update(at(n))
    return row


def _mean(rows: list[dict], key: str) -> float:
    return sum(row[key] for row in rows) / len(rows) if rows else 0.0


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def main() -> None:
    if not GRAPH.exists():
        raise SystemExit(f"frozen graph missing: {GRAPH}. Run tools/eval/final_retrieval.py first.")
    if not (UPSTREAM / "graphify" / "__init__.py").exists():
        raise SystemExit(f"upstream checkout missing: {UPSTREAM}")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    label_to_id = {node.get("label", ""): node["id"] for node in graph["nodes"]}
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    upstream_sha = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    synapse_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    base_env = os.environ.copy()
    base_env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    base_env.pop("PYTHONPATH", None)

    runs = []
    upstream_env = base_env.copy()
    upstream_env["PYTHONPATH"] = str(UPSTREAM)
    for item in questions:
        labels, elapsed, code = _query(
            [sys.executable, "-m", "graphify", "query", item["question"], "--graph", str(GRAPH), "--budget", "2000"],
            upstream_env,
            UPSTREAM,
        )
        row = _score(labels, item["relevant"], label_to_id, 10)
        row.update({"id": item["question"], "returncode": code, "latency_s": elapsed, "retrieved": labels[:10]})
        runs.append(("original_graphify", row, elapsed))

    for mode, flags in SYNAPSE_MODES.items():
        for item in questions:
            labels, elapsed, code = _query(
                [sys.executable, "-m", "graphify", "query", item["question"], "--graph", str(GRAPH), "--budget", "2000", *flags],
                base_env,
                ROOT,
            )
            row = _score(labels, item["relevant"], label_to_id, 10)
            row.update({"id": item["question"], "returncode": code, "latency_s": elapsed, "retrieved": labels[:10]})
            runs.append((mode, row, elapsed))

    by_mode: dict[str, list[dict]] = {}
    latencies: dict[str, list[float]] = {}
    for mode, row, elapsed in runs:
        by_mode.setdefault(mode, []).append(row)
        latencies.setdefault(mode, []).append(elapsed)

    summary = {}
    for mode, rows in by_mode.items():
        summary[mode] = {
            "n": len(rows),
            "precision_at_1": _mean(rows, "precision_at_1"),
            "precision_at_5": _mean(rows, "precision_at_5"),
            "precision_at_10": _mean(rows, "precision_at_10"),
            "recall_at_1": _mean(rows, "recall_at_1"),
            "recall_at_5": _mean(rows, "recall_at_5"),
            "recall_at_10": _mean(rows, "recall_at_10"),
            "mrr": _mean(rows, "mrr_contribution"),
            "latency_median_s": statistics.median(latencies[mode]),
            "latency_p95_s": _pct(latencies[mode], 0.95),
            "errors": sum(1 for row in rows if row["returncode"] != 0),
        }
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark": "graph_retrieval",
        "benchmark_version": 1,
        "completed": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synapse_commit": synapse_sha,
        "original_graphify": {
            "repository": "https://github.com/Graphify-Labs/graphify.git",
            "commit": upstream_sha,
            "note": "Unmodified upstream checkout. Not the Synapse tree.",
        },
        "graph": {"path": str(GRAPH.relative_to(ROOT)).replace("\\", "/"), "nodes": len(graph["nodes"]), "edges": len(graph.get("links") or graph.get("edges") or [])},
        "questions": "tools/eval/ground_truth.json",
        "n_questions": len(questions),
        "metric_notes": "Exact node-id match. No graded labels, so NDCG and MAP are not computed. Learned-weight arm has no feedback file.",
        "summary": summary,
        "tasks": {mode: rows for mode, rows in by_mode.items()},
    }
    (RESULTS / "retrieval_task_results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "retrieval_summary.json").write_text(
        json.dumps({k: payload[k] for k in ("benchmark", "generated_at", "synapse_commit", "original_graphify", "graph", "n_questions", "summary")}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
