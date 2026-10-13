"""Per-task telemetry, retry accounting, and serialization.

Token fields are ``int | None``. A missing provider report is ``None``.
Aggregates do not replace ``None`` with zero. A known count of zero, such as
an adapter that never started an LLM call, is stored only on the call counter
and is separate from token totals.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    counting_method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CallRecord:
    call_id: str
    kind: str
    attempt: int
    status: str
    usage: Usage | None = None
    duration_s: float | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["usage"] = None if self.usage is None else self.usage.to_dict()
        return payload


def empty_usage() -> dict[str, Any]:
    return Usage().to_dict()


def _provider_value(usage: Usage | None, name: str) -> int | None:
    if usage is None or usage.counting_method != "provider":
        return None
    value = getattr(usage, name)
    if value is None:
        return None
    return int(value)


def sum_usage_field(calls: Iterable[CallRecord], name: str, *, kind: str = "llm") -> int | None:
    """Sum one provider-reported field.

    Returns None when there is no matching call or any matching call lacks
    that provider-reported field. Local estimates are not included.
    """
    matched = [call for call in calls if call.kind == kind]
    if not matched:
        return None
    total = 0
    for call in matched:
        value = _provider_value(call.usage, name)
        if value is None:
            return None
        total += value
    return total


def account_retries(calls: list[CallRecord]) -> dict[str, Any]:
    """Count attempts beyond the first for each call id.

    Provider token totals cover LLM calls only. If any LLM call is missing
    provider usage for a field, that field is None rather than a partial sum.
    """
    attempts_by_id: dict[str, int] = {}
    for call in calls:
        attempts_by_id[call.call_id] = attempts_by_id.get(call.call_id, 0) + 1
    extra_attempts = sum(count - 1 for count in attempts_by_id.values())
    failed_attempts = sum(1 for call in calls if call.status in {"error", "retried"})
    llm_calls = [call for call in calls if call.kind == "llm"]
    return {
        "logical_calls": len(attempts_by_id),
        "attempts": len(calls),
        "retries": extra_attempts,
        "failed_or_retried_attempts": failed_attempts,
        "llm_calls": len(llm_calls),
        "tool_calls": sum(1 for call in calls if call.kind != "llm"),
        "retrieval_calls": sum(1 for call in calls if call.kind == "retrieval"),
        "file_reads": sum(1 for call in calls if call.kind == "file_read"),
        "terminal_commands": sum(1 for call in calls if call.kind == "terminal"),
        "usage_complete": all(
            call.usage is not None
            and call.usage.counting_method == "provider"
            and call.usage.input_tokens is not None
            and call.usage.output_tokens is not None
            for call in llm_calls
        )
        if llm_calls
        else False,
        "input_tokens": sum_usage_field(calls, "input_tokens"),
        "output_tokens": sum_usage_field(calls, "output_tokens"),
        "cached_input_tokens": sum_usage_field(calls, "cached_input_tokens"),
        "reasoning_tokens": sum_usage_field(calls, "reasoning_tokens"),
    }


def duplicate_context_ratio(node_ids: list[str]) -> float | None:
    if not node_ids:
        return None
    return 1.0 - (len(set(node_ids)) / len(node_ids))


def relevant_context_ratio(retrieved: list[str], relevant: list[str]) -> float | None:
    if not retrieved or not relevant:
        return None
    relevant_set = set(relevant)
    hits = sum(1 for item in retrieved if item in relevant_set)
    return hits / len(retrieved)


def repeated_action_counts(actions: list[dict[str, str]]) -> dict[str, int]:
    """Count repeated tool names and repeated file-read paths.

    ``actions`` items have ``kind`` and optional ``target``.
    """
    seen: dict[tuple[str, str], int] = {}
    for action in actions:
        key = (action.get("kind", ""), action.get("target", ""))
        seen[key] = seen.get(key, 0) + 1
    repeated = sum(count - 1 for count in seen.values() if count > 1)
    file_reads = [action.get("target", "") for action in actions if action.get("kind") == "file_read"]
    retrievals = [action.get("target", "") for action in actions if action.get("kind") == "retrieval"]
    return {
        "actions": len(actions),
        "repeated_actions": repeated,
        "repeated_file_reads": sum(file_reads.count(path) - 1 for path in set(file_reads) if file_reads.count(path) > 1),
        "repeated_retrievals": sum(
            retrievals.count(question) - 1 for question in set(retrievals) if retrievals.count(question) > 1
        ),
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate agent task records.

    Blocked and unrun rows are excluded from success and token rates. If every
    row in a group lacks a numeric field, the aggregate is None.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record.get("configuration")), []).append(record)

    summary: dict[str, Any] = {}
    for name, rows in groups.items():
        completed = [row for row in rows if row.get("outcome") in {"success", "failure"}]
        successes = [row for row in completed if row.get("outcome") == "success"]
        blocked = [row for row in rows if row.get("outcome") == "blocked"]
        summary[name] = {
            "n": len(rows),
            "completed": len(completed),
            "blocked": len(blocked),
            "successes": len(successes),
            "success_rate": (len(successes) / len(completed)) if completed else None,
            "input_tokens_per_task": _mean_field(completed, "input_tokens"),
            "output_tokens_per_task": _mean_field(completed, "output_tokens"),
            "total_tokens_per_task": _mean_field(completed, "total_tokens"),
            "cost_usd_per_task": _mean_field(completed, "cost_usd"),
            "cost_usd_per_success": _mean_field(successes, "cost_usd"),
            "llm_calls_per_success": _mean_field(successes, "llm_calls"),
            "tool_calls_per_success": _mean_field(successes, "tool_calls"),
            "latency_p50_s": _percentile(
                [float(row["wall_clock_s"]) for row in completed if isinstance(row.get("wall_clock_s"), (int, float))],
                0.50,
            ),
            "latency_p95_s": _percentile(
                [float(row["wall_clock_s"]) for row in completed if isinstance(row.get("wall_clock_s"), (int, float))],
                0.95,
            ),
            "missing_usage": sum(1 for row in rows if row.get("input_tokens") is None),
        }
    return summary


def _mean_field(rows: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            return None
        values.append(float(value))
    return _mean(values)


def relative_change(baseline: float | None, value: float | None) -> dict[str, float | None]:
    if baseline is None or value is None:
        return {"absolute": None, "relative": None}
    absolute = value - baseline
    relative = None if baseline == 0 else absolute / abs(baseline)
    return {"absolute": absolute, "relative": relative}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def usage_from_mapping(payload: dict[str, Any] | None, *, counting_method: str | None) -> Usage:
    """Build a Usage from a provider payload.

    Absent keys stay None. A payload of None yields an empty Usage with no
    counting method, which cannot be priced.
    """
    if not payload:
        return Usage()
    def _optional(name: str) -> int | None:
        if name not in payload or payload[name] is None:
            return None
        return int(payload[name])

    return Usage(
        input_tokens=_optional("input_tokens"),
        output_tokens=_optional("output_tokens"),
        cached_input_tokens=_optional("cached_input_tokens"),
        reasoning_tokens=_optional("reasoning_tokens"),
        counting_method=counting_method if payload else None,
    )
