"""CrossCodeEval-style single-line code-completion accuracy harness.

Every task is answered by the same model under four conditions, so the only
thing that changes between conditions is the cross-file context:

  1. no_context                - only the in-file prefix up to the cursor
  2. bm25_context              - the dataset's own precomputed BM25 cross-file
                                 chunks (the standard CrossCodeEval baseline)
  3. original_graphify_context - `graphify query` from upstream Graphify
                                 (checkout in GRAPHIFY_UPSTREAM, no Synapse flags)
  4. synapse_context           - `graphify query --community-aware` from this repo

Both graph conditions query the same AST-only graph of the task's repository,
built once by this repo (`prepare`), so the comparison isolates retrieval. As
in CrossCodeEval's own BM25 retrieval, snippets from the file being completed
are excluded: the full repository contains the answer line.

Scores exact match and edit similarity (difflib ratio) against `groundtruth`.

Commands (run from the repository root; see docs/eval/DEEPSEEK_EVAL_RUNBOOK.md):

    uv run --with pandas --with pyarrow --with openai python tools/eval/crosscodeeval.py \\
        list-repos --parquet cceval_python.parquet
    ... prepare --parquet P --repos-dir D --repos REPO_ID [REPO_ID ...]
    ... run --parquet P --repos-dir D --repos REPO_ID [...] --n-per-repo 25 \\
        --provider deepseek --out tools/eval/crosscodeeval_results_deepseek.json

`run` writes progress to `<out>.progress.jsonl` after every task and resumes
from it, so an interrupted run can simply be started again.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent
UPSTREAM = Path(os.environ.get("GRAPHIFY_UPSTREAM", Path(os.environ.get("TEMP", "/tmp")) / "graphify-upstream"))

CONDITIONS = ("no_context", "bm25_context", "original_graphify_context", "synapse_context")
MAX_OUTPUT_TOKENS = 300  # one line of code; unchanged from the September runs
SYNAPSE_QUERY_FLAGS = ["--community-aware"]  # what the VS Code extension's Search uses

# OpenAI-compatible providers. DeepSeek runs in non-thinking mode: thinking is
# its default, and hidden reasoning would count against MAX_OUTPUT_TOKENS.
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-flash",
        "extra_body": {"thinking": {"type": "disabled"}},
        # USD per 1M tokens (cache-miss input, output): off-peak, peak.
        "price": {"off_peak": (0.15, 0.60), "peak": (0.30, 1.20)},
    },
    "openai-env": {
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "key_env": "OPENAI_API_KEY",
        "model": os.environ.get("OPENAI_MODEL", "openai/gpt-oss-20b"),
        "extra_body": None,
        "price": None,
    },
}

_STOPWORDS = {
    "self", "the", "and", "for", "with", "import", "from", "return", "def",
    "class", "none", "true", "false", "not", "elif", "else", "if", "while",
}


def query_terms(prompt_tail: str) -> str:
    idents = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", prompt_tail)
    terms = [t for t in idents if t.lower() not in _STOPWORDS]
    # keep it short and most-recent-biased
    return " ".join(terms[-12:])


# --- Dataset ----------------------------------------------------------------------


def load_tasks(parquet: Path):
    import pandas as pd

    return pd.read_parquet(parquet)


def repository(row) -> str:
    return row["metadata"]["repository"]


def task_file(row) -> str:
    meta = row["metadata"]
    return str(meta.get("file") or "").replace("\\", "/") if hasattr(meta, "get") else ""


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


# --- Graph context ----------------------------------------------------------------


def parse_query_nodes(stdout: str, graph_nodes: dict[str, dict]) -> list[tuple[str, str, int]]:
    """(label, source_file, line) for each NODE line. Engines differ in how much
    they print, so a missing src/loc is filled in from graph.json by label."""
    nodes = []
    for line in stdout.splitlines():
        if not line.startswith("NODE "):
            continue
        label = line[5:].split(" [", 1)[0].strip()
        src_m = re.search(r"\bsrc=(\S*)", line)
        loc_m = re.search(r"\bloc=L(\d+)", line)
        src = src_m.group(1) if src_m else ""
        loc = int(loc_m.group(1)) if loc_m else 0
        if (not src or not loc) and label in graph_nodes:
            info = graph_nodes[label]
            src = src or str(info.get("source_file") or "")
            m = re.search(r"L(\d+)", str(info.get("source_location") or ""))
            loc = loc or (int(m.group(1)) if m else 0)
        if src and loc:
            nodes.append((label, src.replace("\\", "/"), loc))
    return nodes


def snippets(repo_dir: Path, nodes: list[tuple[str, str, int]], exclude_file: str, k: int = 5) -> str:
    chunks, seen = [], set()
    for label, src, loc in nodes:
        if len(chunks) >= k:
            break
        if label.endswith(".py") or (exclude_file and src == exclude_file):
            continue
        fpath = repo_dir / src
        if not fpath.is_file():
            continue
        try:
            lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        start = max(0, loc - 1)
        if (src, start) in seen:
            continue
        seen.add((src, start))
        chunks.append(f"# from {src} (near {label})\n" + "\n".join(lines[start : start + 8]))
    return "\n\n".join(chunks)


class GraphContext:
    """Runs one engine's `graphify query` against a repository's graph."""

    def __init__(self, engine: str):
        self.engine = engine
        self._labels: dict[Path, dict[str, dict]] = {}

    def _graph_labels(self, graph: Path) -> dict[str, dict]:
        if graph not in self._labels:
            data = json.loads(graph.read_text(encoding="utf-8"))
            self._labels[graph] = {str(n.get("label")): n for n in data.get("nodes", [])}
        return self._labels[graph]

    def __call__(self, repo_dir: Path, prompt: str, exclude_file: str) -> str:
        graph = repo_dir / "graphify-out" / "graph.json"
        q = query_terms(prompt[-500:])
        if not graph.exists() or not q.strip():
            return ""
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
        for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        cmd = [sys.executable, "-m", "graphify", "query", q, "--graph", str(graph)]
        cwd = REPO_ROOT
        if self.engine == "upstream":
            env["PYTHONPATH"] = str(UPSTREAM)
            cwd = UPSTREAM
        else:
            cmd += SYNAPSE_QUERY_FLAGS
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env)
        return snippets(repo_dir, parse_query_nodes(proc.stdout, self._graph_labels(graph)), exclude_file)


# --- Model ------------------------------------------------------------------------


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


def clean_completion(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)  # strip accidental markdown fences
    text = re.sub(r"\n?```$", "", text)
    return text.splitlines()[0].strip() if text.splitlines() else text


class Model:
    """One completion per call. Failed calls are retried and then reported as
    errors (never scored as wrong answers), and token usage is totalled."""

    def __init__(self, provider: str, model: str | None, dry_run: bool = False):
        self.provider = provider
        cfg = PROVIDERS[provider]
        self.model = model or cfg["model"]
        self.extra_body = cfg["extra_body"]
        self.dry_run = dry_run
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
        self.served_models: set[str] = set()
        if not dry_run:
            from openai import OpenAI

            key = os.environ.get(cfg["key_env"])
            if not key:
                raise SystemExit(f"{cfg['key_env']} is not set. No request was sent.")
            self.client = OpenAI(api_key=key, base_url=cfg["base_url"])

    def peak_cost(self) -> float:
        """Spend so far at the provider's peak price (an upper bound), in USD."""
        price = PROVIDERS[self.provider]["price"]
        if not price:
            return 0.0
        p_in, p_out = price["peak"]
        return self.usage["input_tokens"] / 1e6 * p_in + self.usage["output_tokens"] / 1e6 * p_out

    def complete(self, messages: list[dict], attempts: int = 4) -> tuple[str | None, str | None]:
        if self.dry_run:
            last = messages[-1]["content"].rstrip().splitlines()[-1]
            return last.strip(), None
        error = None
        for attempt in range(attempts):
            try:
                kwargs = {"model": self.model, "messages": messages, "max_tokens": MAX_OUTPUT_TOKENS}
                if self.extra_body:
                    kwargs["extra_body"] = self.extra_body
                resp = self.client.chat.completions.create(**kwargs)
                u = resp.usage
                self.usage["calls"] += 1
                if u is not None:
                    self.usage["input_tokens"] += u.prompt_tokens or 0
                    self.usage["output_tokens"] += u.completion_tokens or 0
                    self.usage["cache_hit_tokens"] += getattr(u, "prompt_cache_hit_tokens", 0) or 0
                if getattr(resp, "model", None):
                    self.served_models.add(resp.model)
                return clean_completion(resp.choices[0].message.content or ""), None
            except Exception as exc:  # noqa: BLE001 - provider errors are retried, then recorded
                error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(30, 2 ** attempt))
        return None, error


def score(pred: str, gold: str) -> dict:
    pred_n = pred.strip()
    gold_n = gold.strip()
    exact = int(pred_n == gold_n)
    sim = difflib.SequenceMatcher(None, pred_n, gold_n).ratio()
    return {"exact_match": exact, "edit_sim": sim}


# --- Commands ---------------------------------------------------------------------


def cmd_list_repos(args) -> None:
    df = load_tasks(args.parquet)
    counts = df["metadata"].apply(lambda m: m["repository"]).value_counts()
    print(f"{len(df)} tasks in {len(counts)} repositories. Largest:")
    for repo_id, n in counts.head(args.top).items():
        print(f"  {n:4d}  {repo_id}")


def resolve_github(repo_id: str) -> tuple[str, str]:
    """`owner-repo-sha` -> (clone URL, sha). Owners and repos may contain '-', so
    each split point is tried against GitHub."""
    *name, sha = repo_id.split("-")
    joined = "-".join(name)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for i, ch in enumerate(joined):
        if ch != "-":
            continue
        url = f"https://github.com/{joined[:i]}/{joined[i + 1:]}.git"
        ok = subprocess.run(["git", "ls-remote", "--heads", url], capture_output=True, env=env).returncode == 0
        if ok:
            return url, sha
    raise SystemExit(f"could not find {repo_id} on GitHub")


def cmd_prepare(args) -> None:
    """Clone each repository at the dataset's commit and build its graph."""
    repo_map = parse_repo_map(args)
    args.repos_dir.mkdir(parents=True, exist_ok=True)
    for repo_id in args.repos:
        repo_dir = args.repos_dir / repo_map.get(repo_id, repo_id)
        if not (repo_dir / ".git").exists():
            url, sha = resolve_github(repo_id)
            print(f"[clone] {url} @ {sha}")
            subprocess.run(["git", "clone", "--quiet", url, str(repo_dir)], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "checkout", "--quiet", sha], check=True)
        if (repo_dir / "graphify-out" / "graph.json").exists() and not args.rebuild:
            print(f"[graph] {repo_id}: exists")
            continue
        if args.rebuild:
            shutil.rmtree(repo_dir / "graphify-out", ignore_errors=True)
        print(f"[graph] {repo_id}: building")
        subprocess.run(
            [sys.executable, "-m", "graphify", "extract", str(repo_dir), "--code-only"],
            cwd=REPO_ROOT, check=True,
        )
    print("Prepared. Next: the `run` command.")


def parse_repo_map(args) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in (args.repo_map or []))


def cmd_run(args) -> None:
    repo_map = parse_repo_map(args)
    df = load_tasks(args.parquet)
    df = df[df["metadata"].apply(lambda m: m["repository"] in set(args.repos))]
    if df.empty:
        raise SystemExit("No tasks matched --repos.")
    missing = [r for r in args.repos if not (args.repos_dir / repo_map.get(r, r) / "graphify-out" / "graph.json").exists()]
    if missing:
        raise SystemExit(f"No graph for {missing}. Run the `prepare` command first.")
    if not (UPSTREAM / "graphify" / "__init__.py").exists():
        raise SystemExit(f"Original Graphify checkout missing: {UPSTREAM} (set GRAPHIFY_UPSTREAM).")

    model = Model(args.provider, args.model, dry_run=args.dry_run)
    engines = {"original_graphify_context": GraphContext("upstream"), "synapse_context": GraphContext("synapse")}
    progress = Path(str(args.out) + ".progress.jsonl")
    rows = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines() if line.strip()] if progress.exists() else []
    done = {r["task_id"] for r in rows}
    for r in rows:  # spend from earlier sessions counts toward --max-cost
        for key, value in (r.get("usage") or {}).items():
            model.usage[key] = model.usage.get(key, 0) + value
    if done:
        print(f"Resuming: {len(done)} tasks already done (${model.peak_cost():.2f} spent, peak-price estimate).")

    selected = []
    for repo_id in args.repos:
        group = df[df["metadata"].apply(lambda m: m["repository"]) == repo_id]
        selected.extend(group.head(args.n_per_repo).iterrows())
    print(f"{len(selected)} tasks x {len(CONDITIONS)} conditions, model {model.model} ({args.provider}).")

    stopped = None
    with progress.open("a", encoding="utf-8") as log:
        for n, (_, r) in enumerate(selected, 1):
            task_id = r["metadata"]["task_id"]
            if task_id in done:
                continue
            if args.max_cost is not None and model.peak_cost() >= args.max_cost:
                stopped = f"spending limit reached: ${model.peak_cost():.2f} (peak-price estimate) >= --max-cost ${args.max_cost:.2f}"
                print(f"\nSTOPPED: {stopped}. Run the same command again later to continue.")
                break
            repo_id = repository(r)
            repo_dir = args.repos_dir / repo_map.get(repo_id, repo_id)
            prompt, gold, own_file = r["prompt"], r["groundtruth"], task_file(r)
            contexts = {
                "no_context": "",
                "bm25_context": bm25_context(r),
                "original_graphify_context": engines["original_graphify_context"](repo_dir, prompt, own_file),
                "synapse_context": engines["synapse_context"](repo_dir, prompt, own_file),
            }
            row = {"task_id": task_id, "repo": repo_id, "gold": gold}
            before = dict(model.usage)
            for cond in CONDITIONS:
                pred, error = model.complete(build_messages(prompt, contexts[cond]))
                row[cond] = {"pred": pred, "error": error, "context_found": bool(contexts[cond])}
                if pred is not None:
                    row[cond].update(score(pred, gold))
            row["usage"] = {k: model.usage[k] - before.get(k, 0) for k in model.usage}
            log.write(json.dumps(row) + "\n")
            log.flush()
            rows.append(row)
            marks = " ".join(
                f"{c.replace('_context', '')}={'ERR' if row[c]['error'] else row[c]['exact_match']}" for c in CONDITIONS
            )
            print(f"[{n}/{len(selected)}] {task_id}: {marks}")

    write_summary(args, model, rows, stopped)


def write_summary(args, model: Model, rows: list[dict], stopped: str | None = None) -> None:
    complete = [r for r in rows if all(r[c].get("error") is None and r[c].get("pred") is not None for c in CONDITIONS)]
    summary: dict = {
        "n_tasks_total": len(rows),
        "n_tasks_scored": len(complete),
        "n_tasks_with_errors": len(rows) - len(complete),
        "note": "Tasks where any condition failed after retries are excluded from every condition, so all conditions are scored on the same tasks.",
    }
    for cond in CONDITIONS:
        n = len(complete) or 1
        summary[cond] = {
            "exact_match": sum(r[cond]["exact_match"] for r in complete) / n,
            "edit_sim": sum(r[cond]["edit_sim"] for r in complete) / n,
            "context_found_rate": sum(r[cond]["context_found"] for r in complete) / n,
        }
    price = PROVIDERS[args.provider]["price"]
    usage = dict(model.usage)
    if price:
        for period, (p_in, p_out) in price.items():
            usage[f"est_cost_usd_{period}"] = round(
                usage["input_tokens"] / 1e6 * p_in + usage["output_tokens"] / 1e6 * p_out, 4
            )
    environment = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider,
        "model_requested": model.model,
        "models_served": sorted(model.served_models),
        "thinking": "disabled" if PROVIDERS[args.provider]["extra_body"] else "provider default",
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "synapse_query_flags": SYNAPSE_QUERY_FLAGS,
        "synapse_commit": _git(REPO_ROOT),
        "upstream_commit": _git(UPSTREAM),
        "repos": args.repos,
        "n_per_repo": args.n_per_repo,
        "dry_run": args.dry_run,
        "max_cost_usd": args.max_cost,
        "stopped": stopped,
        "usage": usage,
    }
    print("\n=== Summary ===")
    print(json.dumps({"summary": summary, "usage": usage}, indent=2))
    args.out.write_text(json.dumps({"summary": summary, "environment": environment, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")


def _git(repo: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = ap.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list-repos", help="show the repositories with the most tasks")
    lst.add_argument("--parquet", required=True, type=Path)
    lst.add_argument("--top", type=int, default=20)

    for name in ("prepare", "run"):
        p = sub.add_parser(name)
        p.add_argument("--parquet", required=name == "run", type=Path)
        p.add_argument("--repos-dir", required=True, type=Path)
        p.add_argument("--repos", nargs="+", required=True, help="dataset repository ids, e.g. nccgroup-libslub-7732a54")
        p.add_argument("--repo-map", nargs="*", help="repo_id=local_dir for repositories cloned under another name")
    prep = sub.choices["prepare"]
    prep.add_argument("--rebuild", action="store_true", help="rebuild graphs that already exist")
    run = sub.choices["run"]
    run.add_argument("--n-per-repo", type=int, default=25)
    run.add_argument("--provider", choices=sorted(PROVIDERS), default="deepseek")
    run.add_argument("--model", default=None, help="override the provider's default model")
    run.add_argument("--out", type=Path, default=HERE / "crosscodeeval_results_deepseek.json")
    run.add_argument("--dry-run", action="store_true", help="no API calls: echo the last prompt line (pipeline check)")
    run.add_argument(
        "--max-cost", type=float, default=None,
        help="stop once this session's spend, estimated at peak prices, reaches this many USD (resume later)",
    )

    args = ap.parse_args()
    {"list-repos": cmd_list_repos, "prepare": cmd_prepare, "run": cmd_run}[args.command](args)


if __name__ == "__main__":
    main()
