"""Retrieval accuracy harness for graphify's graph-based query vs a naive grep baseline.

Runs each ground-truth question through `graphify query` (BFS traversal of the
knowledge graph, no LLM calls) and through a naive keyword-grep baseline over the
same source files, then scores both against hand-labeled relevant node ids using
Precision@K, Recall@K, and MRR.

Usage:
    python tools/eval/retrieval_accuracy.py --graph <graph.json> --corpus <corpus_dir>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def load_ground_truth() -> list[dict]:
    return json.loads((HERE / "ground_truth.json").read_text(encoding="utf-8"))


def load_graph(graph_path: Path) -> dict:
    return json.loads(graph_path.read_text(encoding="utf-8"))


def id_to_label(graph: dict) -> dict[str, str]:
    return {n["id"]: n["label"] for n in graph["nodes"]}


def label_to_id(graph: dict) -> dict[str, str]:
    return {n["label"]: n["id"] for n in graph["nodes"]}


def run_graphify_query(question: str, graph_path: Path) -> list[str]:
    """Run `graphify query` and return the ordered list of retrieved node labels."""
    proc = subprocess.run(
        ["uv", "run", "graphify", "query", question, "--graph", str(graph_path)],
        capture_output=True,
        text=True,
        cwd=HERE.parent.parent,
    )
    labels = []
    for line in proc.stdout.splitlines():
        m = re.match(r"NODE (.+?) \[src=", line)
        if m:
            labels.append(m.group(1))
    return labels


_STOPWORDS = {
    "how", "does", "the", "a", "an", "is", "are", "to", "of", "and", "for",
    "in", "on", "what", "which", "you", "do", "it", "this", "that", "be",
    "over", "from", "with", "before", "being", "into",
}


def naive_grep_baseline(question: str, corpus_dir: Path, label2id: dict[str, str]) -> list[str]:
    """Naive keyword-overlap baseline: score each function/class def line by how
    many non-stopword question terms appear near it (same line or docstring line
    directly after), return labels ranked by score."""
    terms = [t for t in re.findall(r"[a-zA-Z]+", question.lower()) if t not in _STOPWORDS]
    scored: list[tuple[int, str]] = []
    def_re = re.compile(r"^\s*(?:def|class)\s+(\w+)")
    for py_file in sorted(corpus_dir.rglob("*.py")):
        lines = py_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            m = def_re.match(line)
            if not m:
                continue
            name = m.group(1)
            window = " ".join(lines[i : i + 4]).lower()
            score = sum(1 for t in terms if t in window or t in name.lower())
            if score > 0:
                label_paren = f"{name}()"
                label = label_paren if label_paren in label2id else name
                scored.append((score, label))
    scored.sort(key=lambda x: -x[0])
    return [label for _, label in scored]


def precision_recall_mrr(
    retrieved_labels: list[str],
    relevant_ids: list[str],
    label2id: dict[str, str],
    k: int = 10,
) -> dict:
    relevant_set = set(relevant_ids)
    retrieved_ids = [label2id[l] for l in retrieved_labels if l in label2id]
    top_k = retrieved_ids[:k]

    hits = sum(1 for r in top_k if r in relevant_set)
    precision = hits / len(top_k) if top_k else 0.0
    recall = hits / len(relevant_set) if relevant_set else 0.0

    rr = 0.0
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in relevant_set:
            rr = 1.0 / rank
            break

    return {"precision_at_k": precision, "recall_at_k": recall, "rr": rr, "hits": hits}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", type=Path, default=HERE / "retrieval_results.json")
    args = ap.parse_args()

    graph = load_graph(args.graph)
    l2id = label_to_id(graph)
    gt = load_ground_truth()

    rows = []
    for item in gt:
        question = item["question"]
        relevant = item["relevant"]

        graphify_labels = run_graphify_query(question, args.graph)
        graphify_scores = precision_recall_mrr(graphify_labels, relevant, l2id, args.k)

        baseline_labels = naive_grep_baseline(question, args.corpus, l2id)
        baseline_scores = precision_recall_mrr(baseline_labels, relevant, l2id, args.k)

        rows.append(
            {
                "question": question,
                "relevant": relevant,
                "graphify": graphify_scores,
                "grep_baseline": baseline_scores,
            }
        )
        print(
            f"[{'OK' if graphify_scores['hits'] else 'MISS'}] {question[:60]:60s} "
            f"graphify P={graphify_scores['precision_at_k']:.2f} R={graphify_scores['recall_at_k']:.2f} "
            f"RR={graphify_scores['rr']:.2f} | grep P={baseline_scores['precision_at_k']:.2f} "
            f"R={baseline_scores['recall_at_k']:.2f} RR={baseline_scores['rr']:.2f}"
        )

    def agg(key: str, metric: str) -> float:
        return sum(r[key][metric] for r in rows) / len(rows)

    summary = {
        "n_questions": len(rows),
        "graphify": {
            "mean_precision_at_k": agg("graphify", "precision_at_k"),
            "mean_recall_at_k": agg("graphify", "recall_at_k"),
            "mrr": agg("graphify", "rr"),
        },
        "grep_baseline": {
            "mean_precision_at_k": agg("grep_baseline", "precision_at_k"),
            "mean_recall_at_k": agg("grep_baseline", "recall_at_k"),
            "mrr": agg("grep_baseline", "rr"),
        },
    }

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))

    args.out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
