"""Minimal agent adapter for the four comparison configurations.

The adapter calls a live chat provider only when that provider's API key is
set. Otherwise each task is recorded as blocked. Retrieval-only query output
is never stored as an agent answer.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from agent_efficiency.pricing import price_usage
from agent_efficiency.telemetry import CallRecord, Usage, account_retries, empty_usage

SYSTEM_PROMPT = (
    "You are a coding agent answering a question about one repository snapshot. "
    "Use only the tools that are available. Cite repository evidence for every "
    "code claim. If the tools do not contain enough evidence, answer exactly: unknown. "
    "Do not invent files, symbols, or test results."
)

CONFIGURATIONS: dict[str, dict[str, Any]] = {
    "no_graph": {
        "graph_tool": False,
        "engine": None,
        "query_flags": [],
        "description": "File list and file read only. No code-graph query tool.",
    },
    "original_graphify": {
        "graph_tool": True,
        "engine": "upstream",
        "query_flags": [],
        "description": (
            "Same prompt and file tools, plus query_graph implemented by unmodified "
            "upstream Graphify with no Synapse flags."
        ),
    },
    "synapse_default": {
        "graph_tool": True,
        "engine": "synapse",
        "query_flags": [],
        "description": (
            "Synapse query_graph using default weighted traversal. "
            "Personalized PageRank, learned weights, and community-aware retrieval are off."
        ),
    },
    "synapse_full": {
        "graph_tool": True,
        "engine": "synapse",
        "query_flags": ["--community-aware", "--learned-weights", "--ppr"],
        "description": (
            "Synapse query_graph with community-aware retrieval, learned weights, "
            "and Personalized PageRank. PageRank replaces neighborhood ranking, "
            "so this is not the community walk plus PageRank."
        ),
    },
}

PROVIDER_ENV_VARS = ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY")


def provider_status(env: dict[str, str] | None = None) -> dict[str, Any]:
    current = os.environ if env is None else env
    present = [name for name in PROVIDER_ENV_VARS if current.get(name)]
    return {
        "available": bool(present),
        "providers_present": present,
        "model": current.get("OPENAI_MODEL") or None,
        "note": "Key values are not recorded.",
    }


def usage_from_openai_response(response: Any) -> Usage:
    """Map an OpenAI-style response to Usage. Missing fields stay None."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return Usage()

    def _get(obj: Any, name: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    prompt_details = _get(usage, "prompt_tokens_details")
    completion_details = _get(usage, "completion_tokens_details")
    input_tokens = _get(usage, "prompt_tokens")
    if input_tokens is None:
        input_tokens = _get(usage, "input_tokens")
    output_tokens = _get(usage, "completion_tokens")
    if output_tokens is None:
        output_tokens = _get(usage, "output_tokens")
    cached = _get(prompt_details, "cached_tokens")
    reasoning = _get(completion_details, "reasoning_tokens")
    if input_tokens is None and output_tokens is None and cached is None and reasoning is None:
        return Usage()
    return Usage(
        input_tokens=None if input_tokens is None else int(input_tokens),
        output_tokens=None if output_tokens is None else int(output_tokens),
        cached_input_tokens=None if cached is None else int(cached),
        reasoning_tokens=None if reasoning is None else int(reasoning),
        counting_method="provider",
    )


def blocked_task_record(
    *,
    task_id: str,
    configuration: str,
    run_id: str,
    reason: str,
) -> dict[str, Any]:
    """A task the agent did not run. Token fields are null, not zero."""
    config = CONFIGURATIONS[configuration]
    return {
        "task_id": task_id,
        "configuration": configuration,
        "configuration_description": config["description"],
        "run_id": run_id,
        "outcome": "blocked",
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "counting_method": None,
        "usage": empty_usage(),
        "llm_calls": 0,
        "tool_calls": 0,
        "retrieval_calls": 0,
        "file_reads": 0,
        "terminal_commands": 0,
        "repeated_actions": 0,
        "cost_usd": None,
        "wall_clock_s": None,
        "retrieval_latency_s": None,
        "agent_runtime_s": None,
        "retrieved_node_ids": [],
        "relevant_context_ratio": None,
        "duplicate_context_ratio": None,
        "context_tokens_local_estimate": None,
        "final_answer": None,
        "patch": None,
        "test_results": None,
        "failure_reason": reason,
        "correctness": None,
        "calls_started": False,
        "llm_call_count_meaning": (
            "Zero because the adapter did not send a request. "
            "This is not a measured agent run and the token fields stay null."
        ),
    }


def finish_task_record(
    *,
    task_id: str,
    configuration: str,
    run_id: str,
    calls: list[CallRecord],
    outcome: str,
    model: str,
    wall_clock_s: float | None,
    final_answer: str | None,
    failure_reason: str | None,
    retrieved_node_ids: list[str],
    relevant: list[str] | None,
    correctness: str | None,
) -> dict[str, Any]:
    """Build a record from real call telemetry. Do not invent missing usage."""
    from agent_efficiency.telemetry import duplicate_context_ratio, relevant_context_ratio

    totals = account_retries(calls)
    usage = Usage(
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        cached_input_tokens=totals["cached_input_tokens"],
        reasoning_tokens=totals["reasoning_tokens"],
        counting_method="provider" if totals["usage_complete"] else None,
    )
    priced = price_usage(usage, model)
    total_tokens = None
    if totals["input_tokens"] is not None and totals["output_tokens"] is not None:
        total_tokens = totals["input_tokens"] + totals["output_tokens"]
    config = CONFIGURATIONS[configuration]
    return {
        "task_id": task_id,
        "configuration": configuration,
        "configuration_description": config["description"],
        "run_id": run_id,
        "outcome": outcome,
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "total_tokens": total_tokens,
        "counting_method": usage.counting_method,
        "usage": usage.to_dict(),
        "llm_calls": totals["llm_calls"],
        "tool_calls": totals["tool_calls"],
        "retrieval_calls": totals["retrieval_calls"],
        "file_reads": totals["file_reads"],
        "terminal_commands": totals["terminal_commands"],
        "retries": totals["retries"],
        "cost_usd": priced["usd"],
        "cost_detail": priced,
        "wall_clock_s": wall_clock_s,
        "retrieval_latency_s": None,
        "agent_runtime_s": wall_clock_s,
        "retrieved_node_ids": retrieved_node_ids,
        "relevant_context_ratio": relevant_context_ratio(retrieved_node_ids, relevant or []),
        "duplicate_context_ratio": duplicate_context_ratio(retrieved_node_ids),
        "final_answer": final_answer,
        "patch": None,
        "test_results": None,
        "failure_reason": failure_reason,
        "correctness": correctness,
        "calls_started": True,
        "calls": [call.to_dict() for call in calls],
    }


ToolHandler = Callable[[str, dict[str, Any]], str]


def run_scripted_agent(
    *,
    task_id: str,
    configuration: str,
    run_id: str,
    model: str,
    client: Any,
    tool_handler: ToolHandler,
    messages: list[dict[str, Any]],
    max_steps: int = 6,
) -> dict[str, Any]:
    """Drive a chat client that returns OpenAI-shaped responses.

    ``client`` must implement ``complete(messages, tools) -> response``.
    This function is the live path and the test path. It does not fabricate
    usage when the response has none.
    """
    import time

    tools = _tool_schema(configuration)
    calls: list[CallRecord] = []
    retrieved: list[str] = []
    final_answer = None
    failure = None
    outcome = "failure"
    started = time.perf_counter()
    step_messages = list(messages)
    for step in range(max_steps):
        try:
            response = client.complete(step_messages, tools)
        except Exception as exc:  # provider failure is data, not a fake success
            calls.append(
                CallRecord(
                    call_id=f"llm-{step}",
                    kind="llm",
                    attempt=1,
                    status="error",
                    usage=Usage(),
                    detail=type(exc).__name__,
                )
            )
            failure = f"provider_error:{type(exc).__name__}"
            break
        usage = usage_from_openai_response(response)
        calls.append(
            CallRecord(
                call_id=f"llm-{step}",
                kind="llm",
                attempt=1,
                status="ok",
                usage=usage,
            )
        )
        message = _message_from_response(response)
        step_messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            final_answer = message.get("content")
            outcome = "success" if final_answer else "failure"
            if not final_answer:
                failure = "empty_final_answer"
            break
        for index, tool_call in enumerate(tool_calls):
            name = tool_call.get("name")
            arguments = tool_call.get("arguments") or {}
            kind = "retrieval" if name == "query_graph" else "file_read" if name == "read_file" else "other"
            try:
                output = tool_handler(name, arguments)
                status = "ok"
                detail = None
            except Exception as exc:
                output = f"tool_error:{type(exc).__name__}"
                status = "error"
                detail = type(exc).__name__
            calls.append(
                CallRecord(
                    call_id=f"tool-{step}-{index}",
                    kind=kind,
                    attempt=1,
                    status=status,
                    usage=None,
                    detail=detail,
                )
            )
            if name == "query_graph":
                retrieved.extend(_node_labels(output))
            step_messages.append({"role": "tool", "name": name, "content": output})
    else:
        failure = "step_limit"
        outcome = "failure"
    wall = time.perf_counter() - started
    return finish_task_record(
        task_id=task_id,
        configuration=configuration,
        run_id=run_id,
        calls=calls,
        outcome=outcome,
        model=model,
        wall_clock_s=wall,
        final_answer=final_answer,
        failure_reason=failure,
        retrieved_node_ids=retrieved,
        relevant=None,
        correctness=None,
    )


def _tool_schema(configuration: str) -> list[dict[str, Any]]:
    tools = [
        {"name": "list_files", "parameters": {}},
        {"name": "read_file", "parameters": {"path": "string"}},
    ]
    if CONFIGURATIONS[configuration]["graph_tool"]:
        tools.append({"name": "query_graph", "parameters": {"question": "string"}})
    return tools


def _node_labels(text: str) -> list[str]:
    labels = []
    for line in text.splitlines():
        if line.startswith("NODE "):
            labels.append(line.split("[", 1)[0].replace("NODE ", "", 1).strip())
    return labels


def _message_from_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict) and "message" in response:
        return response["message"]
    choices = getattr(response, "choices", None)
    if choices:
        message = choices[0].message
        tool_calls = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = tool_call.function
            tool_calls.append(
                {
                    "name": function.name,
                    "arguments": function.arguments if isinstance(function.arguments, dict) else {},
                }
            )
        return {"role": "assistant", "content": message.content, "tool_calls": tool_calls}
    if isinstance(response, dict):
        return {"role": "assistant", "content": response.get("content"), "tool_calls": response.get("tool_calls") or []}
    return {"role": "assistant", "content": None, "tool_calls": []}
