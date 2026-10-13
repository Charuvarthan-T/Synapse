"""Groq OpenAI-compatible chat client.

The API key is read from GROQ_API_KEY and is never included in returned
records. Rate-limit responses are retried with backoff. Usage from each
attempt is returned even when a later attempt succeeds.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from agent_efficiency.groq_client import parse_groq_usage, redact

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"
PREFERRED_MODELS = (
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
)


class GroqCallError(RuntimeError):
    def __init__(self, message: str, *, status: int | None, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.status = status
        self.attempts = attempts


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "synapse-eval/1.0",
    }


def _rate_headers(headers: Any) -> dict[str, str]:
    kept = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower.startswith("x-ratelimit-") or lower in {"x-request-id", "retry-after"}:
            kept[lower] = value
    return kept


def request_json(
    *,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=_headers(api_key), method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body, _rate_headers(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": {"message": redact(raw)}}
        return exc.code, body, _rate_headers(exc.headers)


def list_model_ids(api_key: str, timeout: float = 30) -> list[str]:
    status, body, _headers_out = request_json(url=MODELS_URL, api_key=api_key, payload=None, timeout=timeout)
    if status != 200:
        message = redact(json.dumps(body)[:500])
        raise GroqCallError(f"model list failed with HTTP {status}: {message}", status=status, attempts=[])
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]


def choose_model(model_ids: list[str]) -> str | None:
    available = set(model_ids)
    for model in PREFERRED_MODELS:
        if model in available:
            return model
    for model in model_ids:
        if "whisper" in model or "tts" in model or "guard" in model or "orpheus" in model:
            continue
        return model
    return None


def complete_chat(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    temperature: float = 0,
    timeout: float = 60,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Send one chat completion, retrying HTTP 429 up to ``max_attempts``."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        status, body, headers = request_json(url=API_URL, api_key=api_key, payload=payload, timeout=timeout)
        parsed = parse_groq_usage(body if isinstance(body, dict) else None)
        request_id = None
        if isinstance(body, dict) and body.get("id"):
            request_id = str(body["id"])
        request_id = headers.get("x-request-id") or request_id
        attempts.append(
            {
                "attempt": attempt,
                "http_status": status,
                "request_id": request_id,
                "model": body.get("model") if isinstance(body, dict) else None,
                "usage": parsed,
                "rate_headers": headers,
            }
        )
        if status == 429 and attempt < max_attempts:
            delay = _retry_delay(headers, attempt)
            time.sleep(delay)
            continue
        if status != 200:
            message = ""
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "")
            raise GroqCallError(
                redact(f"HTTP {status}: {message}") or f"HTTP {status}",
                status=status,
                attempts=attempts,
            )
        message = _assistant_message(body)
        return {
            "message": message,
            "transcript_message": _transcript_message(body),
            "usage": parsed,
            "attempts": attempts,
            "request_id": request_id,
            "model": body.get("model") if isinstance(body, dict) else model,
            "rate_headers": headers,
        }
    raise GroqCallError("retries exhausted", status=429, attempts=attempts)


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    raw = headers.get("retry-after")
    if raw:
        try:
            return min(30.0, max(1.0, float(raw)))
        except ValueError:
            pass
    return min(30.0, float(2 ** attempt))


def _assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = []
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            {
                "id": tool_call.get("id"),
                "name": function.get("name"),
                "arguments": arguments,
            }
        )
    return {"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls}


def _transcript_message(body: dict[str, Any]) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    transcript: dict[str, Any] = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        transcript["tool_calls"] = message["tool_calls"]
    return transcript
