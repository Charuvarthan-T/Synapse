"""Telemetry, pricing, retry, and serialization tests for the agent benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))

from agent_efficiency.adapter import (  # noqa: E402
    blocked_task_record,
    run_scripted_agent,
    usage_from_openai_response,
)
from agent_efficiency.pricing import price_usage  # noqa: E402
from agent_efficiency.telemetry import (  # noqa: E402
    CallRecord,
    Usage,
    account_retries,
    aggregate_records,
    duplicate_context_ratio,
    read_jsonl,
    relative_change,
    repeated_action_counts,
    sum_usage_field,
    write_jsonl,
)


def test_missing_usage_is_not_zero():
    calls = [CallRecord("llm-1", "llm", 1, "ok", usage=Usage())]
    assert sum_usage_field(calls, "input_tokens") is None
    assert account_retries(calls)["input_tokens"] is None
    assert account_retries([])["input_tokens"] is None


def test_retry_sums_complete_provider_usage_and_drops_partial():
    first = CallRecord(
        "answer",
        "llm",
        1,
        "retried",
        usage=Usage(10, 4, 2, 0, "provider"),
    )
    second = CallRecord(
        "answer",
        "llm",
        2,
        "ok",
        usage=Usage(12, 5, 1, 0, "provider"),
    )
    accounted = account_retries([first, second])
    assert accounted["retries"] == 1
    assert accounted["input_tokens"] == 22
    assert accounted["output_tokens"] == 9
    assert accounted["cached_input_tokens"] == 3

    incomplete = CallRecord("answer", "llm", 2, "ok", usage=Usage(input_tokens=12, counting_method="provider"))
    partial = account_retries([first, incomplete])
    assert partial["input_tokens"] == 22
    assert partial["output_tokens"] is None
    assert partial["cached_input_tokens"] is None
    assert partial["usage_complete"] is False


def test_local_estimates_are_excluded_from_provider_totals():
    call = CallRecord("llm-1", "llm", 1, "ok", usage=Usage(100, 20, 0, 0, "local_chars_div_4"))
    assert sum_usage_field([call], "input_tokens") is None


def test_pricing_cached_subset_and_missing_usage():
    priced = price_usage(Usage(1_000_000, 500_000, 250_000, None, "provider"), "openai:gpt-4.1-mini")
    # 750k uncached at $0.40 plus 250k cached at $0.10 plus 500k output at $1.60
    assert priced["usd"] == 0.75 * 0.40 + 0.25 * 0.10 + 0.50 * 1.60
    assert priced["cached_discount_applied"] is True

    missing_cached = price_usage(Usage(1_000_000, 0, None, None, "provider"), "openai:gpt-4.1-mini")
    assert missing_cached["usd"] == 0.40
    assert missing_cached["cached_discount_applied"] is False

    refused = price_usage(Usage(10, 5, 0, 0, "local_chars_div_4"), "openai:gpt-4.1-mini")
    assert refused["usd"] is None
    assert price_usage(Usage(counting_method="provider"), "openai:gpt-4.1-mini")["usd"] is None
    assert price_usage(None, "openai:gpt-4.1-mini")["usd"] is None
    assert price_usage(Usage(1, 1, 0, 0, "provider"), "openai:unknown")["usd"] is None
    assert price_usage(Usage(10, 1, 11, 0, "provider"), "openai:gpt-4.1-mini")["reason"] == (
        "cached_tokens_inconsistent_with_input"
    )


def test_jsonl_preserves_null_usage(tmp_path: Path):
    path = tmp_path / "tasks.jsonl"
    record = blocked_task_record(task_id="q", configuration="no_graph", run_id="r", reason="missing key")
    write_jsonl(path, [record])
    loaded = read_jsonl(path)
    assert loaded[0]["input_tokens"] is None
    assert loaded[0]["cost_usd"] is None
    assert loaded[0]["outcome"] == "blocked"
    assert "null" in path.read_text(encoding="utf-8")


def test_aggregate_blocked_rows_have_no_success_rate():
    rows = [
        blocked_task_record(task_id="q", configuration="synapse_default", run_id="a", reason="blocked"),
        blocked_task_record(task_id="q2", configuration="synapse_default", run_id="b", reason="blocked"),
    ]
    summary = aggregate_records(rows)["synapse_default"]
    assert summary["success_rate"] is None
    assert summary["cost_usd_per_task"] is None
    assert summary["blocked"] == 2
    assert summary["missing_usage"] == 2


def test_openai_response_without_usage_stays_empty():
    usage = usage_from_openai_response(type("Response", (), {"usage": None})())
    assert usage.input_tokens is None
    assert usage.counting_method is None
    assert price_usage(usage, "openai:gpt-4.1-mini")["usd"] is None


def test_scripted_agent_records_provider_usage_and_tool_retries():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return {
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "prompt_tokens_details": {}},
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"name": "query_graph", "arguments": {"question": "alpha"}}],
                    },
                }
            return {
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
                "message": {"role": "assistant", "content": "unknown", "tool_calls": []},
            }

    def tools(name, arguments):
        assert name == "query_graph"
        return "NODE alpha [src=a.py loc=L1]\n"

    record = run_scripted_agent(
        task_id="q",
        configuration="synapse_default",
        run_id="run",
        model="openai:gpt-4.1-mini",
        client=Client(),
        tool_handler=tools,
        messages=[{"role": "user", "content": "q"}],
    )
    assert record["outcome"] == "success"
    assert record["retrieved_node_ids"] == ["alpha"]
    assert record["llm_calls"] == 2
    assert record["retrieval_calls"] == 1
    assert record["input_tokens"] == 50
    assert record["output_tokens"] == 12
    # The first response omitted cached tokens, so the cached total stays null
    # and the cache discount is not applied.
    assert record["cached_input_tokens"] is None
    assert record["reasoning_tokens"] is None
    assert record["cost_detail"]["cached_discount_applied"] is False
    assert record["cost_usd"] == (50 * 0.40 + 12 * 1.60) / 1_000_000
    assert record["correctness"] is None


def test_provider_error_is_not_a_zero_token_success():
    class Client:
        def complete(self, messages, tools):
            raise RuntimeError("offline")

    record = run_scripted_agent(
        task_id="q",
        configuration="no_graph",
        run_id="run",
        model="openai:gpt-4.1-mini",
        client=Client(),
        tool_handler=lambda name, arguments: "",
        messages=[],
    )
    assert record["outcome"] == "failure"
    assert record["input_tokens"] is None
    assert record["cost_usd"] is None
    assert record["failure_reason"] == "provider_error:RuntimeError"


def test_duplicate_ratio_and_repeated_actions():
    assert abs(duplicate_context_ratio(["a", "a", "b"]) - (1 / 3)) < 1e-12
    assert duplicate_context_ratio([]) is None
    counts = repeated_action_counts(
        [
            {"kind": "file_read", "target": "a.py"},
            {"kind": "file_read", "target": "a.py"},
            {"kind": "retrieval", "target": "same question"},
            {"kind": "retrieval", "target": "same question"},
        ]
    )
    assert counts["repeated_file_reads"] == 1
    assert counts["repeated_retrievals"] == 1
    change = relative_change(0.4, 0.5)
    assert abs(change["absolute"] - 0.1) < 1e-12
    assert abs(change["relative"] - 0.25) < 1e-12
    assert relative_change(None, 0.5)["relative"] is None
