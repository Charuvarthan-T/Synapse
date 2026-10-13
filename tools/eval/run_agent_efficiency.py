"""Run the agent-efficiency benchmark.

Live agent tasks run only when a provider key is set. Graph indexing, query
latency, retrieval-context budgets, and reliability probes are measured
separately and are not reported as agent success or API cost.

    python tools/eval/run_agent_efficiency.py
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from agent_efficiency.adapter import (  # noqa: E402
    CONFIGURATIONS,
    SYSTEM_PROMPT,
    blocked_task_record,
    provider_status,
)
from agent_efficiency.lifecycle import copy_python_files, extract_corpus, query_latency  # noqa: E402
from agent_efficiency.pricing import PRICE_TABLE, PRICING_AS_OF, PRICING_SOURCE  # noqa: E402
from agent_efficiency.reliability import (  # noqa: E402
    probe_concurrent_queries,
    probe_corrupt_graph,
    probe_interrupted_extract,
    probe_missing_graph,
    probe_repeated_query,
    probe_stale_graph,
    probe_validation_levels,
)
from agent_efficiency.telemetry import (  # noqa: E402
    aggregate_records,
    duplicate_context_ratio,
    relative_change,
    relevant_context_ratio,
    write_json,
    write_jsonl,
)

RESULTS = HERE / "results" / "agent_efficiency"
GRAPH = HERE / "frozen_corpus" / "graphify-out" / "graph.json"
QUESTIONS = HERE / "ground_truth.json"
UPSTREAM = Path(os.environ.get("GRAPHIFY_UPSTREAM", Path(os.environ.get("TEMP", "/tmp")) / "graphify-upstream"))
BUDGETS = (2000, 4000, 8000, 16000)
RETRIEVAL_CONFIGS = {
    "original_graphify": ("upstream", []),
    "synapse_default": ("synapse", []),
    "synapse_community": ("synapse", ["--community-aware"]),
    "synapse_ppr": ("synapse", ["--ppr"]),
    "synapse_full": ("synapse", ["--community-aware", "--learned-weights", "--ppr"]),
}


def _git(repo: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _labels(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        if line.startswith("NODE "):
            found.append(line.split("[", 1)[0].replace("NODE ", "", 1).strip())
    return found


def _score(retrieved_ids: list[str], relevant: list[str]) -> dict[str, float | None]:
    relevant_set = set(relevant)

    def at(n: int) -> dict[str, float]:
        window = retrieved_ids[:n]
        hits = sum(1 for item in window if item in relevant_set)
        return {
            f"precision_at_{n}": (hits / len(window)) if window else 0.0,
            f"recall_at_{n}": (hits / len(relevant_set)) if relevant_set else 0.0,
        }

    reciprocal = 0.0
    for rank, item in enumerate(retrieved_ids, start=1):
        if item in relevant_set:
            reciprocal = 1.0 / rank
            break
    row: dict[str, float | None] = {"mrr": reciprocal}
    for n in (1, 5, 10):
        row.update(at(n))
    return row


def _query(engine: str, question: str, budget: int, flags: list[str]) -> dict:
    env = os.environ.copy()
    env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("GRAPHIFY_OUT", None)
    cwd = ROOT
    if engine == "upstream":
        env["PYTHONPATH"] = str(UPSTREAM)
        cwd = UPSTREAM
    cmd = [
        sys.executable,
        "-m",
        "graphify",
        "query",
        question,
        "--graph",
        str(GRAPH),
        "--budget",
        str(budget),
        *flags,
    ]
    import time

    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    ok = proc.returncode == 0
    labels = _labels(proc.stdout) if ok else []
    return {
        "returncode": proc.returncode,
        "latency_s": elapsed,
        "labels": labels,
        "context_tokens_local_estimate": (max(1, len(proc.stdout) // 4) if ok else None),
        "counting_method": "local_chars_div_4" if ok else None,
        "provider_tokens": None,
        "stderr_tail": proc.stderr[-240:],
    }


def _environment(synapse_sha: str | None, upstream_sha: str | None) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "synapse_commit": synapse_sha,
        "upstream_repository": "https://github.com/Graphify-Labs/graphify.git",
        "upstream_commit": upstream_sha,
        "upstream_checkout": str(UPSTREAM),
        "upstream_present": (UPSTREAM / "graphify" / "__init__.py").exists(),
        "frozen_graph": str(GRAPH.relative_to(ROOT)).replace("\\", "/") if GRAPH.exists() else None,
        "frozen_graph_present": GRAPH.exists(),
        "provider": provider_status(),
        "system_prompt_sha256_note": "The same SYSTEM_PROMPT is defined for every configuration.",
        "system_prompt": SYSTEM_PROMPT,
        "configurations": CONFIGURATIONS,
        "pricing": {
            "source": PRICING_SOURCE,
            "as_of": PRICING_AS_OF,
            "tier": "standard",
            "models": list(PRICE_TABLE),
            "applied_to_a_live_run": False,
        },
        "token_estimate": "Retrieved context uses len(text)//4 from graphify's local estimator. It is not a provider token count.",
    }


def _agent_records(questions: list[dict]) -> list[dict]:
    if provider_status()["available"]:
        raise RuntimeError(
            "A provider key is set. Refusing to mark the agent batch blocked. "
            "Wire run_scripted_agent to that provider before publishing results."
        )
    reason = (
        "BLOCKED: no OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY. "
        "The agent adapter did not send a request. "
        "CrossCodeEval parquet is also absent, so coding-task success was not scored."
    )
    records = []
    for config in CONFIGURATIONS:
        for index, item in enumerate(questions):
            records.append(
                blocked_task_record(
                    task_id=item["question"],
                    configuration=config,
                    run_id=f"{config}-{index}",
                    reason=reason,
                )
            )
    return records


def _budget_sweep(questions: list[dict], label_to_id: dict[str, str]) -> list[dict]:
    jobs = []
    for config, (engine, flags) in RETRIEVAL_CONFIGS.items():
        if engine == "upstream" and not (UPSTREAM / "graphify" / "__init__.py").exists():
            continue
        for budget in BUDGETS:
            for item in questions:
                jobs.append((config, engine, flags, budget, item))

    def one(job: tuple) -> dict:
        config, engine, flags, budget, item = job
        raw = _query(engine, item["question"], budget, flags)
        ids = [label_to_id.get(label, label) for label in raw["labels"]]
        row = {
            "experiment": "retrieval_context_budget",
            "agent_result": False,
            "task_id": item["question"],
            "configuration": config,
            "budget_tokens": budget,
            "returncode": raw["returncode"],
            "retrieval_latency_s": raw["latency_s"],
            "retrieved_node_ids": ids,
            "context_tokens_local_estimate": raw["context_tokens_local_estimate"],
            "counting_method": raw["counting_method"],
            "provider_input_tokens": None,
            "provider_output_tokens": None,
            "api_cost_usd": None,
            "agent_success": None,
            "duplicate_context_ratio": duplicate_context_ratio(ids) if raw["returncode"] == 0 else None,
            "relevant_context_ratio": relevant_context_ratio(ids, item["relevant"]) if raw["returncode"] == 0 else None,
        }
        if raw["returncode"] == 0:
            row.update(_score(ids, item["relevant"]))
        else:
            row["error"] = raw["stderr_tail"]
            for key in ("precision_at_1", "precision_at_5", "precision_at_10", "recall_at_1", "recall_at_5", "recall_at_10", "mrr"):
                row[key] = None
        return row

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, job) for job in jobs]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row["configuration"], row["budget_tokens"], row["task_id"]))
    return rows


def _mean(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    if len(values) != len(rows) or not rows:
        return None
    return sum(values) / len(rows)


def _summarize_sweep(rows: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for row in rows:
        summary.setdefault(row["configuration"], {}).setdefault(str(row["budget_tokens"]), []).append(row)
    out: dict[str, dict] = {}
    for config, budgets in summary.items():
        out[config] = {}
        for budget, group in budgets.items():
            out[config][budget] = {
                "n": len(group),
                "errors": sum(1 for row in group if row["returncode"] != 0),
                "precision_at_10": _mean(group, "precision_at_10"),
                "recall_at_10": _mean(group, "recall_at_10"),
                "mrr": _mean(group, "mrr"),
                "context_tokens_local_estimate_mean": _mean(group, "context_tokens_local_estimate"),
                "duplicate_context_ratio_mean": _mean(group, "duplicate_context_ratio"),
                "relevant_context_ratio_mean": _mean(group, "relevant_context_ratio"),
                "retrieval_latency_p50_s": _percentile([row["retrieval_latency_s"] for row in group], 0.50),
                "agent_success": None,
                "api_cost_usd": None,
            }
    return out


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _lifecycle(question: str) -> dict:
    frozen_files = sorted((HERE / "frozen_corpus" / "graphify").glob("*.py"))
    package_files = sorted(path for path in (ROOT / "graphify").glob("*.py"))
    sizes = {
        "frozen_1": frozen_files[:1],
        "frozen_7": frozen_files,
        "package_sample": package_files[:25],
    }
    indexing = []
    incremental = []
    with tempfile.TemporaryDirectory(prefix="synapse-agent-eff-") as tmp:
        root = Path(tmp)
        for name, files in sizes.items():
            source = root / name
            copy_python_files(files, source)
            for engine in ("synapse", "upstream"):
                if engine == "upstream" and not (UPSTREAM / "graphify" / "__init__.py").exists():
                    indexing.append({"corpus": name, "engine": engine, "status": "blocked", "reason": "upstream checkout missing"})
                    continue
                if engine == "upstream" and name == "package_sample":
                    continue
                measured = extract_corpus(
                    source=source,
                    out_dir=root / f"out-{name}-{engine}",
                    engine=engine,
                    upstream=UPSTREAM,
                    repo=ROOT,
                )
                measured["corpus"] = name
                indexing.append(measured)
                if name == "frozen_7" and engine == "synapse" and measured["returncode"] == 0:
                    changed = source / files[0].name
                    changed.write_text(changed.read_text(encoding="utf-8") + "\n# touch\n", encoding="utf-8")
                    second = extract_corpus(
                        source=source,
                        out_dir=root / f"out-{name}-{engine}",
                        engine=engine,
                        upstream=UPSTREAM,
                        repo=ROOT,
                        fresh=False,
                    )
                    second["corpus"] = name
                    second["pass"] = "incremental_after_one_file_touch"
                    incremental.append({"first_wall_clock_s": measured["wall_clock_s"], "second": second})
    latency = []
    if GRAPH.exists():
        for config, (engine, flags) in (
            ("original_graphify", ("upstream", [])),
            ("synapse_default", ("synapse", [])),
            ("synapse_full", ("synapse", ["--community-aware", "--learned-weights", "--ppr"])),
        ):
            if engine == "upstream" and not (UPSTREAM / "graphify" / "__init__.py").exists():
                continue
            latency.append(
                {
                    "configuration": config,
                    **query_latency(
                        question=question,
                        graph=GRAPH,
                        engine=engine,
                        upstream=UPSTREAM,
                        repo=ROOT,
                        flags=flags,
                        repeats=5,
                        warmup=1,
                    ),
                }
            )
    return {
        "kind": "graph_lifecycle_not_agent",
        "api_cost_usd": None,
        "break_even_tasks": None,
        "break_even_reason": "API cost was not measured, so a dollar break-even is not computed.",
        "indexing": indexing,
        "incremental": incremental,
        "query_latency": latency,
    }


def _reliability(question: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="synapse-reliability-") as tmp:
        work = Path(tmp)
        source = work / "interrupt_src"
        copy_python_files(sorted((HERE / "frozen_corpus" / "graphify").glob("*.py")), source)
        probes = [
            probe_missing_graph(ROOT, work),
            probe_corrupt_graph(ROOT, work),
            probe_stale_graph(ROOT, work),
            probe_validation_levels(),
            probe_interrupted_extract(ROOT, source, work),
        ]
    if GRAPH.exists():
        probes.append(probe_concurrent_queries(ROOT, GRAPH, question))
        probes.append(probe_repeated_query(ROOT, GRAPH, question))
    return {"kind": "graph_reliability_not_agent", "probes": probes}


def _naive_corpus_estimate() -> dict:
    files = sorted((HERE / "frozen_corpus" / "graphify").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    return {
        "kind": "naive_full_corpus_local_estimate",
        "agent_result": False,
        "files": len(files),
        "context_tokens_local_estimate": max(1, len(text) // 4),
        "counting_method": "local_chars_div_4",
        "provider_tokens": None,
        "api_cost_usd": None,
        "agent_success": None,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys and not isinstance(row[key], (list, dict)):
                keys.append(key)
    lines = [",".join(keys)]
    for row in rows:
        cells = []
        for key in keys:
            value = row.get(key)
            if value is None:
                cells.append("")
            else:
                cells.append(str(value).replace(",", " "))
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    synapse_sha = _git(ROOT)
    upstream_sha = _git(UPSTREAM) if UPSTREAM.exists() else None
    environment = _environment(synapse_sha, upstream_sha)
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if not GRAPH.exists():
        raise SystemExit(f"frozen graph missing: {GRAPH}")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    label_to_id = {node.get("label", ""): node["id"] for node in graph["nodes"]}

    agent_rows = _agent_records(questions)
    write_jsonl(RESULTS / "agent_tasks.jsonl", agent_rows)
    agent_summary = aggregate_records(agent_rows)
    write_json(RESULTS / "agent_summary.json", {
        "benchmark": "agent_efficiency",
        "status": "BLOCKED",
        "reason": agent_rows[0]["failure_reason"] if agent_rows else "no tasks",
        "n_tasks_per_configuration": len(questions),
        "configurations": list(CONFIGURATIONS),
        "summary": agent_summary,
        "not_agent_measurements": "Indexing, budget, and reliability files in this directory are separate.",
    })

    sweep_rows = _budget_sweep(questions, label_to_id)
    write_jsonl(RESULTS / "retrieval_budget_tasks.jsonl", sweep_rows)
    sweep_summary = _summarize_sweep(sweep_rows)
    write_json(RESULTS / "retrieval_budget_summary.json", {
        "benchmark": "retrieval_context_budget",
        "status": "COMPLETE",
        "agent_success": None,
        "api_cost_usd": None,
        "note": "Local context-token estimates and retrieval metrics only. Not an agent run.",
        "budgets": list(BUDGETS),
        "summary": sweep_summary,
        "versus_upstream_recall_at_10": {
            config: {
                budget: relative_change(
                    sweep_summary.get("original_graphify", {}).get(budget, {}).get("recall_at_10"),
                    sweep_summary.get(config, {}).get(budget, {}).get("recall_at_10"),
                )
                for budget in sweep_summary.get(config, {})
            }
            for config in sweep_summary
            if config != "original_graphify"
        },
    })
    flat_summary = []
    for config, budgets in sweep_summary.items():
        for budget, metrics in budgets.items():
            flat_summary.append({"configuration": config, "budget_tokens": int(budget), **metrics})
    _write_csv(RESULTS / "retrieval_budget_summary.csv", flat_summary)

    lifecycle = _lifecycle(questions[0]["question"])
    write_json(RESULTS / "lifecycle.json", lifecycle)
    reliability = _reliability(questions[0]["question"])
    write_json(RESULTS / "reliability.json", reliability)
    naive = _naive_corpus_estimate()
    write_json(RESULTS / "naive_corpus_estimate.json", naive)

    prior = HERE / "results" / "retrieval_summary.json"
    ablation = {
        "status": "RETRIEVAL_ONLY",
        "agent_ablation": "BLOCKED",
        "reason": "No provider credentials were used. Learned weights were not trained on the 20 labels.",
        "prior_retrieval_summary": str(prior.relative_to(ROOT)).replace("\\", "/") if prior.exists() else None,
        "learned_weights": "Not run. No genuine feedback file. Benchmark labels were not used as training data.",
        "synapse_full_note": "--ppr replaces neighborhood ranking, including when community-aware is also set.",
    }
    write_json(RESULTS / "ablation_status.json", ablation)
    write_json(RESULTS / "correctness_status.json", {
        "status": "BLOCKED",
        "agent_unsupported_claim_rate": None,
        "reason": "No live agent answers were produced. Existing graph-evidence checks in tools/eval/results/reasoning_results.json are not agent results.",
        "crosscodeeval": "BLOCKED",
        "crosscodeeval_reason": "No CrossCodeEval parquet in the repository and no provider key.",
    })
    write_json(RESULTS / "environment.json", environment)
    write_json(RESULTS / "manifest.json", {
        "benchmark": "agent_efficiency",
        "benchmark_version": 1,
        "generated_at": environment["generated_at"],
        "synapse_commit": synapse_sha,
        "upstream_commit": upstream_sha,
        "experiments": {
            "A_token_and_cost": "BLOCKED",
            "B_context_budget_agent_success": "BLOCKED",
            "B_retrieval_context_budget": "COMPLETE",
            "C_agent_tool_efficiency": "BLOCKED",
            "D_indexing_break_even_wall_clock": "COMPLETE",
            "D_indexing_break_even_dollars": "BLOCKED",
            "E_agent_ablation": "BLOCKED",
            "E_retrieval_ablation_reference": "RETRIEVAL_ONLY",
            "F_repository_scale": "COMPLETE",
            "G_graph_reliability": "COMPLETE",
            "H_agent_correctness": "BLOCKED",
        },
    })
    print(json.dumps({"agent": "BLOCKED", "sweep_groups": len(flat_summary), "commit": synapse_sha}, indent=2))


if __name__ == "__main__":
    main()
