"""Groq usage parsing, retry accounting, and serialization."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))

from agent_efficiency.groq_client import (  # noqa: E402
    observed_totals,
    paired_savings,
    parse_groq_usage,
    redact,
    savings_pct,
    summarize_tasks,
    write_jsonl,
)
from agent_efficiency.groq_http import choose_model  # noqa: E402


def test_missing_usage_stays_null():
    parsed = parse_groq_usage({"id": "req"})
    assert parsed["input_tokens"] is None
    assert parsed["output_tokens"] is None
    assert parsed["usage_complete"] is False
    assert parsed["counting_method"] is None


def test_provider_usage_keeps_absent_cached_tokens_null():
    parsed = parse_groq_usage(
        {
            "usage": {"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135},
            "id": "chatcmpl-1",
        }
    )
    assert parsed["input_tokens"] == 120
    assert parsed["output_tokens"] == 15
    assert parsed["total_tokens"] == 135
    assert parsed["cached_input_tokens"] is None
    assert parsed["reasoning_tokens"] is None
    assert parsed["usage_complete"] is True
    assert parsed["counting_method"] == "provider"


def test_retry_without_usage_does_not_zero_the_successful_call():
    calls = [
        {"kind": "llm", "status": "retried", "usage": parse_groq_usage({})},
        {"kind": "llm", "status": "ok", "usage": parse_groq_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 4}})},
    ]
    ok = [call for call in calls if call["status"] == "ok"]
    totals = observed_totals(ok)
    assert totals["input_tokens"] == 10
    assert totals["output_tokens"] == 4
    assert totals["cached_input_tokens"] is None
    missing = observed_totals(calls)
    assert missing["input_tokens"] is None
    assert missing["input_tokens_observed"] == 10
    assert missing["calls_missing_input"] == 1


def test_savings_and_paired_comparison():
    assert savings_pct(200, 150) == 25
    assert savings_pct(None, 10) is None
    records = [
        {"task_id": "q", "configuration": "original_graphify", "outcome": "success", "usage_complete": True, "input_tokens": 100, "output_tokens": 20, "citation": "cited_relevant_label"},
        {"task_id": "q", "configuration": "synapse_default", "outcome": "success", "usage_complete": True, "input_tokens": 40, "output_tokens": 20, "citation": "not_cited"},
        {"task_id": "other", "configuration": "synapse_default", "outcome": "blocked", "usage_complete": False, "input_tokens": None, "output_tokens": None},
    ]
    paired = paired_savings(records, "original_graphify", "synapse_default")
    assert paired["n_paired"] == 1
    assert paired["total_savings_pct"] == 50
    assert paired["input_savings_pct"] == 60
    summary = summarize_tasks(records)
    assert summary["original_graphify"]["input_tokens_total"] == 100
    assert summary["synapse_default"]["blocked"] == 1
    assert summary["synapse_default"]["input_tokens_mean"] == 40
    assert summary["synapse_default"]["completed"] == 1


def test_redaction_and_jsonl_nulls(tmp_path: Path):
    assert "gsk_" not in (redact("token gsk_exampleSecretValue") or "")
    assert "org_" not in (redact("organization org_01example") or "")
    path = tmp_path / "tasks.jsonl"
    write_jsonl(path, [{"input_tokens": None, "outcome": "blocked"}])
    assert '"input_tokens": null' in path.read_text(encoding="utf-8")


def test_model_choice_prefers_the_small_free_model():
    chosen = choose_model(["whisper-large-v3", "openai/gpt-oss-20b", "llama-3.1-8b-instant"])
    assert chosen == "llama-3.1-8b-instant"
    assert choose_model(["whisper-large-v3"]) is None
