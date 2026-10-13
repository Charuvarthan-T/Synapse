"""Parse and aggregate Groq provider usage.

Missing fields stay None. A free-tier dollar cost of zero is recorded only
by the caller after a response actually contains token counts. This module
does not apply a paid price table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_efficiency.telemetry import Usage

_SECRET = re.compile(r"(gsk_[A-Za-z0-9]+|Bearer\s+\S+|org_[A-Za-z0-9]+)", re.IGNORECASE)


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    return _SECRET.sub("[redacted]", text)


def parse_groq_usage(body: dict[str, Any] | None) -> dict[str, Any]:
    """Read usage from a chat-completion body. Absent fields stay None."""
    empty = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "counting_method": None,
        "usage_complete": False,
    }
    if not isinstance(body, dict):
        return empty
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return empty

    def optional(*names: str) -> int | None:
        for name in names:
            if name in usage and usage[name] is not None:
                return int(usage[name])
        return None

    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    cached = None
    if isinstance(prompt_details, dict) and prompt_details.get("cached_tokens") is not None:
        cached = int(prompt_details["cached_tokens"])
    reasoning = None
    if isinstance(completion_details, dict) and completion_details.get("reasoning_tokens") is not None:
        reasoning = int(completion_details["reasoning_tokens"])
    prompt = optional("prompt_tokens", "input_tokens")
    completion = optional("completion_tokens", "output_tokens")
    complete = prompt is not None and completion is not None
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": optional("total_tokens"),
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
        "counting_method": "provider" if complete else None,
        "usage_complete": complete,
    }


def usage_object(parsed: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=parsed["input_tokens"],
        output_tokens=parsed["output_tokens"],
        cached_input_tokens=parsed["cached_input_tokens"],
        reasoning_tokens=parsed["reasoning_tokens"],
        counting_method=parsed["counting_method"],
    )


def observed_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum provider tokens that were actually reported.

    Calls that omit a field do not contribute a zero. If any LLM call omits
    input or output, the complete total for that field is None and the
    observed sum is reported separately.
    """
    llm = [call for call in calls if call.get("kind") == "llm"]
    if not llm:
        return {
            "llm_calls": 0,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "input_tokens_observed": None,
            "output_tokens_observed": None,
            "calls_missing_input": 0,
            "calls_missing_output": 0,
            "usage_complete": False,
        }

    def values(name: str) -> tuple[list[int], int]:
        present: list[int] = []
        missing = 0
        for call in llm:
            usage = call.get("usage") or {}
            value = usage.get(name)
            if value is None:
                missing += 1
            else:
                present.append(int(value))
        return present, missing

    inputs, missing_in = values("input_tokens")
    outputs, missing_out = values("output_tokens")
    totals, missing_total = values("total_tokens")
    cached, missing_cached = values("cached_input_tokens")
    reasoning, missing_reasoning = values("reasoning_tokens")
    return {
        "llm_calls": len(llm),
        "input_tokens": sum(inputs) if inputs and missing_in == 0 else None,
        "output_tokens": sum(outputs) if outputs and missing_out == 0 else None,
        "total_tokens": sum(totals) if totals and missing_total == 0 else None,
        "cached_input_tokens": sum(cached) if cached and missing_cached == 0 else None,
        "reasoning_tokens": sum(reasoning) if reasoning and missing_reasoning == 0 else None,
        "input_tokens_observed": sum(inputs) if inputs else None,
        "output_tokens_observed": sum(outputs) if outputs else None,
        "calls_missing_input": missing_in,
        "calls_missing_output": missing_out,
        "usage_complete": missing_in == 0 and missing_out == 0,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def summarize_tasks(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate completed Groq tasks. Blocked rows stay out of the means."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record.get("configuration")), []).append(record)
    summary: dict[str, Any] = {}
    for name, rows in groups.items():
        completed = [row for row in rows if row.get("outcome") in {"success", "failure"}]
        successes = [row for row in completed if row.get("outcome") == "success"]
        cited = [row for row in completed if row.get("citation") == "cited_relevant_label"]
        inputs = [row["input_tokens"] for row in completed if isinstance(row.get("input_tokens"), int)]
        outputs = [row["output_tokens"] for row in completed if isinstance(row.get("output_tokens"), int)]
        calls = [row["llm_calls"] for row in completed if isinstance(row.get("llm_calls"), int)]
        tools = [row["tool_calls"] for row in completed if isinstance(row.get("tool_calls"), int)]
        complete_usage = [row for row in completed if row.get("usage_complete") is True]
        summary[name] = {
            "attempted": len(rows),
            "completed": len(completed),
            "successes": len(successes),
            "failures": sum(1 for row in completed if row.get("outcome") == "failure"),
            "blocked": sum(1 for row in rows if row.get("outcome") == "blocked"),
            "usage_complete": len(complete_usage),
            "usage_incomplete": sum(1 for row in completed if row.get("usage_complete") is not True),
            "success_rate": (len(successes) / len(completed)) if completed else None,
            "citation_rate": (len(cited) / len(completed)) if completed else None,
            "test_pass_rate": None,
            "test_pass_rate_reason": "These questions have no executable test oracle.",
            "input_tokens_mean": _mean([float(value) for value in inputs]) if len(inputs) == len(completed) and completed else None,
            "input_tokens_median": _median([float(value) for value in inputs]) if len(inputs) == len(completed) and completed else None,
            "output_tokens_mean": _mean([float(value) for value in outputs]) if len(outputs) == len(completed) and completed else None,
            "output_tokens_median": _median([float(value) for value in outputs]) if len(outputs) == len(completed) and completed else None,
            "input_tokens_total": sum(inputs) if len(inputs) == len(completed) and completed else None,
            "output_tokens_total": sum(outputs) if len(outputs) == len(completed) and completed else None,
            "llm_calls_mean": _mean([float(value) for value in calls]) if len(calls) == len(completed) and completed else None,
            "llm_calls_median": _median([float(value) for value in calls]) if len(calls) == len(completed) and completed else None,
            "tool_calls_mean": _mean([float(value) for value in tools]) if len(tools) == len(completed) and completed else None,
        }
        if summary[name]["input_tokens_total"] is not None and summary[name]["output_tokens_total"] is not None:
            summary[name]["provider_tokens_total"] = (
                summary[name]["input_tokens_total"] + summary[name]["output_tokens_total"]
            )
        else:
            summary[name]["provider_tokens_total"] = None
    return summary


def savings_pct(baseline: float | None, value: float | None) -> float | None:
    if baseline is None or value is None or baseline == 0:
        return None
    return 100.0 * (baseline - value) / baseline


def paired_savings(records: list[dict[str, Any]], baseline: str, candidate: str) -> dict[str, Any]:
    """Compare tasks that both configurations finished with complete usage."""
    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        if record.get("usage_complete") is not True:
            continue
        if record.get("outcome") not in {"success", "failure"}:
            continue
        by_task.setdefault(str(record.get("task_id")), {})[str(record.get("configuration"))] = record
    pairs = []
    for task_id, configs in by_task.items():
        if baseline in configs and candidate in configs:
            pairs.append((task_id, configs[baseline], configs[candidate]))
    if not pairs:
        return {"n_paired": 0, "input_savings_pct": None, "output_savings_pct": None, "total_savings_pct": None}
    base_in = sum(item[1]["input_tokens"] for item in pairs)
    cand_in = sum(item[2]["input_tokens"] for item in pairs)
    base_out = sum(item[1]["output_tokens"] for item in pairs)
    cand_out = sum(item[2]["output_tokens"] for item in pairs)
    base_total = base_in + base_out
    cand_total = cand_in + cand_out
    base_cited = sum(1 for item in pairs if item[1].get("citation") == "cited_relevant_label")
    cand_cited = sum(1 for item in pairs if item[2].get("citation") == "cited_relevant_label")
    return {
        "n_paired": len(pairs),
        "baseline_input_tokens": base_in,
        "candidate_input_tokens": cand_in,
        "baseline_output_tokens": base_out,
        "candidate_output_tokens": cand_out,
        "baseline_total_tokens": base_total,
        "candidate_total_tokens": cand_total,
        "input_savings_pct": savings_pct(base_in, cand_in),
        "output_savings_pct": savings_pct(base_out, cand_out),
        "total_savings_pct": savings_pct(base_total, cand_total),
        "baseline_citations": base_cited,
        "candidate_citations": cand_cited,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
