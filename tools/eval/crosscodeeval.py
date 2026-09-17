"""CrossCodeEval-style single-line code-completion accuracy harness.

Compares three conditions for line completion on real CrossCodeEval Python
tasks, each drawn from a repo we've cloned at the exact commit the dataset
was built from:

  1. no_context   - only the in-file prefix up to the cursor
  2. bm25_context - the dataset's own precomputed BM25 cross-file chunks
                     (the standard CrossCodeEval baseline)
  3. graphify     - cross-file chunks retrieved via `graphify query` against
                     a real AST-only graph of the same repo/commit

Scores exact match and edit similarity (difflib ratio) against `groundtruth`.

Usage:
    python tools/eval/crosscodeeval.py --parquet <path> --repos-dir <dir> \
        --repo-map nccgroup-libslub-7732a54=libslub vladkens-twscrape-4f2ee7f=twscrape \
        --n-per-repo 24 --out tools/eval/crosscodeeval_results.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
from openai import OpenAI

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent

_STOPWORDS = {
    "self", "the", "and", "for", "with", "import", "from", "return", "def",
    "class", "none", "true", "false", "not", "elif", "else", "if", "while",
}


def query_terms(prompt_tail: str) -> str:
    idents = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", prompt_tail)
    terms = [t for t in idents if t.lower() not in _STOPWORDS]
    # keep it short and most-recent-biased
    return " ".join(terms[-12:])


def graphify_context(repo_dir: Path, prompt: str, k: int = 5) -> str:
    """Run graphify query against the repo's graph and materialize a small
    cross-file context block from the top retrieved non-file nodes."""
    graph_path = repo_dir / "graphify-out" / "graph.json"
    if not graph_path.exists():
        return ""
    q = query_terms(prompt[-500:])
    if not q.strip():
        return ""
    proc = subprocess.run(
        ["uv", "run", "graphify", "query", q, "--graph", str(graph_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    nodes = []
    for line in proc.stdout.splitlines():
        m = re.match(r"NODE (.+?) \[src=(\S*) loc=L(\d+)", line)
        if m:
            label, src, loc = m.group(1), m.group(2), int(m.group(3))
            if src and not label.endswith(".py"):
                nodes.append((label, src, loc))

    chunks = []
    seen_files = set()
    for label, src, loc in nodes:
        if len(chunks) >= k:
            break
        fpath = repo_dir / src
        if not fpath.exists():
            continue
        try:
            lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        start = max(0, loc - 1)
        end = min(len(lines), start + 8)
        snippet = "\n".join(lines[start:end])
        key = (src, start)
        if key in seen_files:
            continue
        seen_files.add(key)
        chunks.append(f"# from {src} (near {label})\n{snippet}")

    if not chunks:
        return ""
    return "\n\n".join(chunks)


def bm25_context(row, k: int = 5) -> str:
    ctx = row.get("crossfile_context_retrieval")
    if ctx is None:
        return ""
    items = ctx.get("list") if isinstance(ctx, dict) else ctx
    if items is None:
        return ""
    chunks = []
    for item in list(items)[:k]:
        fname = item.get("filename", "?")
        chunk = item.get("retrieved_chunk", "")
        chunks.append(f"# from {fname}\n{chunk}")
    return "\n\n".join(chunks)


def build_messages(prompt: str, context: str) -> list[dict]:
    prefix_tail = "\n".join(prompt.splitlines()[-40:])
    if context:
        user = (
            "Relevant code from other files in this repository:\n\n"
            f"{context}\n\n"
            "Now complete the next line of code in the file below. "
            "Reply with ONLY that one line of code, no explanation, no markdown fences.\n\n"
            f"{prefix_tail}"
        )
    else:
        user = (
            "Complete the next line of code in the file below. "
            "Reply with ONLY that one line of code, no explanation, no markdown fences.\n\n"
            f"{prefix_tail}"
        )
    return [{"role": "user", "content": user}]


def call_llm(client: OpenAI, model: str, messages: list[dict]) -> str:
    try:
        resp = client.chat.completions.create(model=model, messages=messages, max_tokens=300)
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - provider hiccups shouldn't kill the run
        print(f"    [warn] LLM call failed: {exc}", file=sys.stderr)
        return ""
    # strip accidental markdown fences
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.splitlines()[0].strip() if text.splitlines() else text


def score(pred: str, gold: str) -> dict:
    pred_n = pred.strip()
    gold_n = gold.strip()
    exact = int(pred_n == gold_n)
    sim = difflib.SequenceMatcher(None, pred_n, gold_n).ratio()
    return {"exact_match": exact, "edit_sim": sim}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True, type=Path)
    ap.add_argument("--repos-dir", required=True, type=Path)
    ap.add_argument(
        "--repo-map",
        nargs="+",
        required=True,
        help="dataset_repo_id=local_dir_name pairs",
    )
    ap.add_argument("--n-per-repo", type=int, default=24)
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "openai/gpt-oss-20b"))
    ap.add_argument("--out", type=Path, default=HERE / "crosscodeeval_results.json")
    args = ap.parse_args()

    repo_map = dict(kv.split("=", 1) for kv in args.repo_map)

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )

    df = pd.read_parquet(args.parquet)
    df = df[df["metadata"].apply(lambda m: m["repository"] in repo_map)]

    rows = []
    for repo_id, group in df.groupby(df["metadata"].apply(lambda m: m["repository"])):
        repo_dir = args.repos_dir / repo_map[repo_id]
        sample = group.head(args.n_per_repo)
        for _, r in sample.iterrows():
            prompt = r["prompt"]
            gold = r["groundtruth"]
            task_id = r["metadata"]["task_id"]

            no_ctx_pred = call_llm(client, args.model, build_messages(prompt, ""))
            bm25_ctx = bm25_context(r)
            bm25_pred = call_llm(client, args.model, build_messages(prompt, bm25_ctx))
            gf_ctx = graphify_context(repo_dir, prompt)
            gf_pred = call_llm(client, args.model, build_messages(prompt, gf_ctx))

            row = {
                "task_id": task_id,
                "repo": repo_id,
                "gold": gold,
                "no_context": {"pred": no_ctx_pred, **score(no_ctx_pred, gold)},
                "bm25_context": {"pred": bm25_pred, **score(bm25_pred, gold)},
                "graphify_context": {"pred": gf_pred, **score(gf_pred, gold)},
                "graphify_ctx_found": bool(gf_ctx),
            }
            rows.append(row)
            print(
                f"[{task_id}] no_ctx={row['no_context']['exact_match']} "
                f"bm25={row['bm25_context']['exact_match']} "
                f"graphify={row['graphify_context']['exact_match']} "
                f"(gf_ctx={'yes' if gf_ctx else 'no'})"
            )

    def agg(cond: str, metric: str) -> float:
        return sum(r[cond][metric] for r in rows) / len(rows)

    summary = {"n_tasks": len(rows)}
    for cond in ("no_context", "bm25_context", "graphify_context"):
        summary[cond] = {
            "exact_match": agg(cond, "exact_match"),
            "edit_sim": agg(cond, "edit_sim"),
        }

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))

    args.out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
