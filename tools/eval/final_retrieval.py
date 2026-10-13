"""Final retrieval comparison for the integrated Synapse engine.

Runs the frozen seven-file corpus through the current CLI. It does not read
benchmark labels into weight learning. Original Graphify numbers in
``retrieval_results_existing_graphify.json`` stay historical; this file records
only what the final commit produces.

    python tools/eval/final_retrieval.py
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
CORPUS = HERE / "frozen_corpus"
GRAPH = CORPUS / "graphify-out" / "graph.json"
SOURCE_FILES = [
    "graphify/build.py",
    "graphify/serve.py",
    "graphify/security.py",
    "graphify/export.py",
    "graphify/benchmark.py",
    "graphify/paths.py",
    "graphify/__main__.py",
]
MODES = {
    "unweighted": ["--unweighted"],
    "weighted": [],
    "community": ["--community-aware"],
    "ppr": ["--ppr"],
    "learned": ["--learned-weights"],
    "all_new": ["--ppr", "--learned-weights", "--community-aware"],
}


def freeze_corpus() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for rel in SOURCE_FILES:
        src = ROOT / rel
        dest = CORPUS / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def extract_graph() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "extract", str(CORPUS), "--code-only", "--no-cluster"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not GRAPH.exists():
        raise SystemExit(proc.stderr or proc.stdout or "extract failed")


def query(question: str, flags: list[str]) -> tuple[list[str], float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "query", question, "--graph", str(GRAPH), *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    labels = []
    for line in proc.stdout.splitlines():
        if line.startswith("NODE "):
            label = line.split("[", 1)[0].replace("NODE ", "", 1).strip()
            labels.append(label)
    return labels, elapsed


def score(retrieved: list[str], relevant: list[str], label_to_id: dict[str, str], k: int = 10) -> dict:
    relevant_set = set(relevant)
    ids = []
    for label in retrieved:
        if label in label_to_id:
            ids.append(label_to_id[label])
        else:
            ids.append(label)
    top = ids[:k]
    hits = sum(1 for item in top if item in relevant_set)
    precision = hits / len(top) if top else 0.0
    recall = hits / len(relevant_set) if relevant_set else 0.0
    rr = 0.0
    for rank, item in enumerate(ids, start=1):
        if item in relevant_set:
            rr = 1.0 / rank
            break
    return {
        "precision_at_k": precision,
        "recall_at_k": recall,
        "mrr_contribution": rr,
        "exact_id_hits": hits,
        "hits": hits,
    }


def mean(rows: list[dict], key: str) -> float:
    return sum(row[key] for row in rows) / len(rows) if rows else 0.0


def main() -> None:
    freeze_corpus()
    extract_graph()
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    label_to_id = {node.get("label", ""): node["id"] for node in graph["nodes"]}
    questions = json.loads((HERE / "ground_truth.json").read_text(encoding="utf-8"))
    summaries = {}
    details = []
    for name, flags in MODES.items():
        latencies = []
        mode_rows = []
        for item in questions:
            labels, elapsed = query(item["question"], flags)
            latencies.append(elapsed)
            row = score(labels, item["relevant"], label_to_id)
            row["question"] = item["question"]
            mode_rows.append(row)
        summaries[name] = {
            "precision_at_10": mean(mode_rows, "precision_at_k"),
            "recall_at_10": mean(mode_rows, "recall_at_k"),
            "mrr": mean(mode_rows, "mrr_contribution"),
            "exact_id_hits_total": sum(row["exact_id_hits"] for row in mode_rows),
            "latency_median_s": statistics.median(latencies),
            "latency_p95_s": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)],
        }
        details.append({"mode": name, "rows": mode_rows})
    out = {
        "corpus_files": SOURCE_FILES,
        "nodes": len(graph["nodes"]),
        "edges": len(graph.get("links") or graph.get("edges") or []),
        "ground_truth": "tools/eval/ground_truth.json",
        "ground_truth_audit": (
            "The 20 questions and target ids in ground_truth.json are the original "
            "team-authored set. They were not relabelled for this run. Scoring uses "
            "exact node ids only. Learned weights are enabled with no feedback file, "
            "so that arm is the fixed-weight fallback and is not tuned on these labels."
        ),
        "summary": summaries,
        "modes": details,
    }
    dest = HERE / "final_retrieval_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"nodes": out["nodes"], "edges": out["edges"], "summary": summaries}, indent=2))


if __name__ == "__main__":
    main()
