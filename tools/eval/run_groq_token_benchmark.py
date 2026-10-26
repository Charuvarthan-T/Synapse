"""Run the Groq free-tier agent token benchmark.

The key is read from GROQ_API_KEY and is not written to the result files.
A one-question pilot runs unless --all is passed. The process stops after a
rate limit or payment error and leaves the remaining tasks blocked.

    python tools/eval/run_groq_token_benchmark.py
    python tools/eval/run_groq_token_benchmark.py --all
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from agent_efficiency.adapter import CONFIGURATIONS, SYSTEM_PROMPT  # noqa: E402
from agent_efficiency.groq_client import (  # noqa: E402
    observed_totals,
    paired_savings,
    redact,
    summarize_tasks,
    write_json,
    write_jsonl,
)
from agent_efficiency.groq_http import (  # noqa: E402
    GroqCallError,
    choose_model,
    complete_chat,
    provider,
    provider_name,
    list_model_ids,
)

RESULTS = HERE / "results" / "agent_efficiency"  # one subfolder per provider
USER_INSTRUCTION = (
    " Answer in at most 120 words and cite the function or symbol names the snapshot supports."
    " If the query_graph tool is available, call it once with this question before answering."
    " If query_graph is not available, use list_files and read_file."
)
GRAPH = HERE / "frozen_corpus" / "graphify-out" / "graph.json"
CORPUS = HERE / "frozen_corpus" / "graphify"
QUESTIONS = HERE / "ground_truth.json"
UPSTREAM = Path(os.environ.get("GRAPHIFY_UPSTREAM", Path(os.environ.get("TEMP", "/tmp")) / "graphify-upstream"))
MAX_STEPS = 3
MAX_OUTPUT_TOKENS = 320
QUERY_BUDGET = 800
TOOL_CHAR_CAP = 6000
MODEL_GAP_S = 2.5


def _git(repo: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _labels_for(graph: dict, relevant_ids: list[str]) -> list[str]:
    by_id = {node.get("id"): node.get("label") or "" for node in graph.get("nodes") or []}
    labels = []
    for node_id in relevant_ids:
        label = str(by_id.get(node_id) or "").strip()
        if label:
            labels.append(label)
    return labels


def citation_of(answer: str | None, labels: list[str]) -> str | None:
    """Whether the answer names a ground-truth node label.

    This is a citation check, not an executable test result.
    """
    if not labels:
        return None
    if not answer:
        return "not_cited"
    folded = answer.lower()
    for label in labels:
        needle = label.lower().replace("()", "").strip()
        if len(needle) >= 4 and needle in folded:
            return "cited_relevant_label"
    return "not_cited"


def _cap(text: str) -> str:
    if len(text) <= TOOL_CHAR_CAP:
        return text
    omitted = len(text) - TOOL_CHAR_CAP
    return text[:TOOL_CHAR_CAP] + f"\n[truncated {omitted} characters]"


def _tool_schema(configuration: str) -> list[dict]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List the Python files in the frozen repository snapshot.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read one Python file from the frozen snapshot. The result may be truncated.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if CONFIGURATIONS[configuration]["graph_tool"]:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "query_graph",
                    "description": "Query the code graph for the frozen snapshot and return matching nodes.",
                    "parameters": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _query_graph(configuration: str, question: str) -> tuple[str, list[str]]:
    config = CONFIGURATIONS[configuration]
    env = os.environ.copy()
    env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("GROQ_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    cwd = ROOT
    if config["engine"] == "upstream":
        env["PYTHONPATH"] = str(UPSTREAM)
        cwd = UPSTREAM
    started = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "graphify",
            "query",
            question,
            "--graph",
            str(GRAPH),
            "--budget",
            str(QUERY_BUDGET),
            *config["query_flags"],
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - started
    text = proc.stdout if proc.returncode == 0 else proc.stderr
    labels = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("NODE "):
            labels.append(line.split("[", 1)[0].replace("NODE ", "", 1).strip())
    body = f"[query exit {proc.returncode} in {elapsed:.3f}s]\n{_cap(text or '')}"
    return body, labels


def _read_file(path_arg: str) -> str:
    raw = Path(path_arg)
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = (CORPUS / raw.name).resolve()
    try:
        candidate.relative_to(CORPUS.resolve())
    except ValueError:
        return "error: path is outside the frozen snapshot"
    if not candidate.is_file():
        return "error: file not in the frozen snapshot"
    return _cap(candidate.read_text(encoding="utf-8", errors="replace"))


def _dispatch(configuration: str, name: str, arguments: dict) -> tuple[str, list[str]]:
    if name == "list_files":
        names = sorted(path.name for path in CORPUS.glob("*.py"))
        return "\n".join(names), []
    if name == "read_file":
        return _read_file(str(arguments.get("path") or "")), []
    if name == "query_graph":
        question = str(arguments.get("question") or "")
        return _query_graph(configuration, question)
    return f"error: unknown tool {name}", []


def _pace(rate_headers: dict[str, str]) -> None:
    """Stay inside the live token window. A short gap also limits request rate."""
    remaining = rate_headers.get("x-ratelimit-remaining-tokens")
    reset = rate_headers.get("x-ratelimit-reset-tokens") or ""
    wait = MODEL_GAP_S
    try:
        left = float(remaining) if remaining is not None else None
    except ValueError:
        left = None
    if left is not None and left < 2500:
        wait = max(wait, _reset_seconds(reset) + 0.5)
    time.sleep(wait)


def _reset_seconds(text: str) -> float:
    text = text.strip().lower()
    if not text:
        return 15.0
    if text.endswith("ms"):
        return float(text[:-2]) / 1000
    minutes = 0.0
    if "m" in text and text.endswith("s"):
        minute_text, second_text = text[:-1].split("m", 1)
        minutes = float(minute_text or 0)
        return minutes * 60 + float(second_text or 0)
    if text.endswith("s"):
        return float(text[:-1])
    return 15.0


def run_task(
    *,
    api_key: str,
    model: str,
    configuration: str,
    task: dict,
    run_id: str,
    label_by_id: dict[str, str],
) -> tuple[dict, list[dict], dict[str, str]]:
    labels = [label_by_id[item] for item in task["relevant"] if item in label_by_id]
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                task["question"]
                + USER_INSTRUCTION
            ),
        },
    ]
    tools = _tool_schema(configuration)
    calls: list[dict] = []
    retrieved: list[str] = []
    final_answer = None
    failure = None
    outcome = "failure"
    last_headers: dict[str, str] = {}
    started = time.perf_counter()
    for step in range(MAX_STEPS):
        try:
            response = complete_chat(
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except GroqCallError as exc:
            for attempt in exc.attempts:
                calls.append(
                    {
                        "kind": "llm",
                        "call_id": f"llm-{step}",
                        "attempt": attempt["attempt"],
                        "status": "retried" if attempt["http_status"] == 429 and attempt is not exc.attempts[-1] else "error",
                        "http_status": attempt["http_status"],
                        "request_id": attempt.get("request_id"),
                        "model": attempt.get("model") or model,
                        "usage": attempt.get("usage"),
                    }
                )
            failure = redact(str(exc))
            outcome = "blocked" if exc.status == 429 else "failure"
            break
        usage = response["usage"]
        calls.append(
            {
                "kind": "llm",
                "call_id": f"llm-{step}",
                "attempt": len(response["attempts"]),
                "status": "ok",
                "http_status": 200,
                "request_id": response.get("request_id"),
                "model": response.get("model") or model,
                "usage": usage,
            }
        )
        for prior in response["attempts"][:-1]:
            calls.append(
                {
                    "kind": "llm",
                    "call_id": f"llm-{step}-retry",
                    "attempt": prior["attempt"],
                    "status": "retried",
                    "http_status": prior["http_status"],
                    "request_id": prior.get("request_id"),
                    "model": prior.get("model") or model,
                    "usage": prior.get("usage"),
                }
            )
        last_headers = response.get("rate_headers") or {}
        message = response["message"]
        messages.append(response["transcript_message"])
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            final_answer = message.get("content")
            outcome = "success" if final_answer else "failure"
            if not final_answer:
                failure = "empty_final_answer"
            break
        for index, tool_call in enumerate(tool_calls):
            name = tool_call.get("name") or ""
            output, nodes = _dispatch(configuration, name, tool_call.get("arguments") or {})
            retrieved.extend(nodes)
            kind = "retrieval" if name == "query_graph" else "file_read" if name == "read_file" else "other"
            calls.append(
                {
                    "kind": kind,
                    "call_id": f"tool-{step}-{index}",
                    "attempt": 1,
                    "status": "ok",
                    "tool": name,
                    "usage": None,
                }
            )
            tool_message = {"role": "tool", "content": output}
            if tool_call.get("id"):
                tool_message["tool_call_id"] = tool_call["id"]
            messages.append(tool_message)
        _pace(last_headers)
    else:
        failure = "step_limit"
        outcome = "failure"
    ok_llm = [call for call in calls if call["kind"] == "llm" and call["status"] == "ok"]
    totals = observed_totals(ok_llm or [call for call in calls if call["kind"] == "llm"])
    record = {
        "task_id": task["question"],
        "configuration": configuration,
        "run_id": run_id,
        "model": model,
        "outcome": outcome,
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "total_tokens": totals["total_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "input_tokens_observed": totals["input_tokens_observed"],
        "output_tokens_observed": totals["output_tokens_observed"],
        "calls_missing_input": totals["calls_missing_input"],
        "calls_missing_output": totals["calls_missing_output"],
        "usage_complete": totals["usage_complete"] if ok_llm else False,
        "counting_method": "provider" if ok_llm and totals["usage_complete"] else None,
        "llm_calls": sum(1 for call in calls if call["kind"] == "llm"),
        "tool_calls": sum(1 for call in calls if call["kind"] != "llm"),
        "retrieval_calls": sum(1 for call in calls if call["kind"] == "retrieval"),
        "file_reads": sum(1 for call in calls if call["kind"] == "file_read"),
        "retries": sum(1 for call in calls if call.get("status") == "retried"),
        "cost_usd": 0 if ok_llm and totals["usage_complete"] else None,
        "cost_basis": "groq_free_tier_no_invoice" if ok_llm and totals["usage_complete"] else None,
        "wall_clock_s": time.perf_counter() - started,
        "retrieved_node_ids": retrieved,
        "final_answer": final_answer,
        "failure_reason": failure,
        "citation": citation_of(final_answer, labels) if outcome == "success" else citation_of(final_answer, labels),
        "relevant_labels": labels,
        "test_results": None,
        "correctness_note": "citation_of_relevant_label is not a unit-test pass.",
        "calls": calls,
    }
    call_rows = []
    for call in calls:
        call_rows.append(
            {
                "task_id": task["question"],
                "configuration": configuration,
                "run_id": run_id,
                **{key: value for key, value in call.items() if key != "usage"},
                **(call.get("usage") or {}),
            }
        )
    return record, call_rows, last_headers


def _blocked(task: dict, configuration: str, run_id: str, model: str, reason: str) -> dict:
    return {
        "task_id": task["question"],
        "configuration": configuration,
        "run_id": run_id,
        "model": model,
        "outcome": "blocked",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "usage_complete": False,
        "counting_method": None,
        "llm_calls": 0,
        "tool_calls": 0,
        "retrieval_calls": 0,
        "file_reads": 0,
        "retries": 0,
        "cost_usd": None,
        "final_answer": None,
        "failure_reason": reason,
        "citation": None,
        "test_results": None,
        "calls": [],
    }


def main() -> None:
    run_all = "--all" in sys.argv
    key_env = provider()["key_env"]
    api_key = os.environ.get(key_env, "")
    if not api_key:
        raise SystemExit(f"{key_env} is not set. No request was sent.")
    if not GRAPH.exists():
        raise SystemExit(f"frozen graph missing: {GRAPH}")
    if not (UPSTREAM / "graphify" / "__init__.py").exists():
        raise SystemExit(f"upstream checkout missing: {UPSTREAM}")
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if not run_all:
        questions = questions[:1]
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    label_by_id = {node.get("id"): node.get("label") or "" for node in graph.get("nodes") or []}
    model_ids = list_model_ids(api_key)
    model = choose_model(model_ids)
    if model is None:
        raise SystemExit("No chat model was returned for this key. No benchmark task was sent.")
    probe = complete_chat(
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word pong."}],
        tools=None,
        max_tokens=8,
    )
    if not probe["usage"]["usage_complete"]:
        raise SystemExit("The probe completion did not include input and output token counts. Benchmark not started.")
    out = RESULTS / provider_name() / ("full_graph_instruction" if run_all else "pilot_graph_instruction")
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    call_rows: list[dict] = []
    stop_reason = None
    all_questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    selected = {item["question"] for item in questions}
    for configuration in CONFIGURATIONS:
        for index, task in enumerate(all_questions):
            run_id = f"{configuration}-{index}"
            if task["question"] not in selected:
                continue
            if stop_reason:
                records.append(_blocked(task, configuration, run_id, model, stop_reason))
                continue
            record, rows, _headers = run_task(
                api_key=api_key,
                model=model,
                configuration=configuration,
                task=task,
                run_id=run_id,
                label_by_id=label_by_id,
            )
            records.append(record)
            call_rows.extend(rows)
            write_jsonl(out / "tasks.jsonl", records)
            write_jsonl(out / "calls.jsonl", call_rows)
            if record["outcome"] == "blocked" and record.get("failure_reason"):
                stop_reason = record["failure_reason"]
            else:
                _pace({})
    if not run_all:
        for configuration in CONFIGURATIONS:
            for index, task in enumerate(all_questions):
                if task["question"] in selected:
                    continue
                records.append(
                    _blocked(
                        task,
                        configuration,
                        f"{configuration}-{index}",
                        model,
                        "BLOCKED: held for the full run. The pilot does not include this question.",
                    )
                )
    attempted = [row for row in records if "held for the full run" not in str(row.get("failure_reason"))]
    summary = summarize_tasks(attempted)
    success_rows = [row for row in attempted if row.get("outcome") == "success"]
    comparisons = {
        "all_completed_usage": {
            "synapse_default_vs_original_graphify": paired_savings(attempted, "original_graphify", "synapse_default"),
            "synapse_full_vs_original_graphify": paired_savings(attempted, "original_graphify", "synapse_full"),
            "no_graph_vs_original_graphify": paired_savings(attempted, "original_graphify", "no_graph"),
        },
        "success_only": {
            "synapse_default_vs_original_graphify": paired_savings(success_rows, "original_graphify", "synapse_default"),
            "synapse_full_vs_original_graphify": paired_savings(success_rows, "original_graphify", "synapse_full"),
            "no_graph_vs_original_graphify": paired_savings(success_rows, "original_graphify", "no_graph"),
        },
    }
    environment = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "synapse_commit": _git(ROOT),
        "upstream_commit": _git(UPSTREAM),
        "upstream_repository": "https://github.com/Graphify-Labs/graphify.git",
        "model": model,
        "models_visible": model_ids,
        "probe_usage": probe["usage"],
        "probe_request_id": probe.get("request_id"),
        "probe_rate_headers": probe.get("rate_headers"),
        "max_steps": MAX_STEPS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "query_budget": QUERY_BUDGET,
        "tool_char_cap": TOOL_CHAR_CAP,
        "temperature": 0,
        "system_prompt": SYSTEM_PROMPT,
        "provider": provider_name(),
        "thinking": provider()["extra"].get("thinking", {}).get("type", "provider default"),
        "api_key_present": True,
        "cost_basis": (
            "Free-tier requests are recorded as 0 USD when usage is complete. This is not an invoice and the paid price table is not applied."
            if provider_name() == "groq"
            else "Paid API. Cost = provider token counts x the provider's list price; the provider's usage page is the invoice."
        ),
        "counting_method": "provider",
        "local_char_estimate_used": False,
        "mode": "all" if run_all else "pilot",
        "stop_reason": stop_reason,
    }
    write_json(out / "environment.json", environment)
    write_json(out / "summary.json", {"summary": summary, "comparisons": comparisons, "stop_reason": stop_reason})
    write_jsonl(out / "tasks.jsonl", records)
    write_jsonl(out / "calls.jsonl", call_rows)
    print(json.dumps({"model": model, "mode": environment["mode"], "stop_reason": stop_reason, "summary": summary, "comparisons": comparisons}, indent=2))


if __name__ == "__main__":
    main()
