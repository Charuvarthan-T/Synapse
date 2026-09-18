"""Tests for the editor-bridge LLM backend (prompts answered by the host editor)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import networkx as nx
import pytest

from graphify import llm
from graphify.bidirectional_reasoner import reason
from graphify.semantic_extraction import BackendLLMProvider

TOKEN = "test-token"


class _Bridge:
    """Minimal stand-in for the VS Code extension's bridge server."""

    def __init__(self, reply):
        self.reply = reply
        self.requests: list[dict] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server API
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bridge.requests.append(
                    {"auth": self.headers.get("Authorization"), "body": body}
                )
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    status, payload = 401, {"error": "unauthorized"}
                else:
                    status, payload = bridge.reply(body)
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/complete"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def bridge_env(monkeypatch):
    def activate(url, token=TOKEN):
        monkeypatch.setenv(llm.ENV_BRIDGE_URL, url)
        monkeypatch.setenv(llm.ENV_BRIDGE_TOKEN, token)

    return activate


def test_editor_bridge_is_registered_and_never_auto_detected(monkeypatch):
    assert "editor-bridge" in llm.BACKENDS
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AZURE_OPENAI_API_KEY", "AWS_PROFILE",
                "AWS_REGION", "AWS_DEFAULT_REGION", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(llm.ENV_BRIDGE_URL, "http://127.0.0.1:1/complete")
    assert llm.detect_backend() != "editor-bridge"


def test_call_llm_round_trips_through_bridge(bridge_env):
    with _Bridge(lambda body: (200, {"text": "hello", "usage": {"input_tokens": 7,
                                                                "output_tokens": 2}})) as b:
        bridge_env(b.url)
        usage: dict = {}
        out = llm._call_llm("ping", backend="editor-bridge", max_tokens=55, usage_out=usage)
    assert out == "hello"
    assert usage == {"input": 7, "output": 2}
    assert b.requests[0]["auth"] == f"Bearer {TOKEN}"
    assert b.requests[0]["body"] == {"prompt": "ping", "max_tokens": 55, "model": None}


def test_bridge_error_message_is_surfaced(bridge_env):
    with _Bridge(lambda body: (503, {"error": "Copilot access was denied"})) as b:
        bridge_env(b.url)
        with pytest.raises(RuntimeError, match="Copilot access was denied"):
            llm._call_llm("ping", backend="editor-bridge")


def test_wrong_token_is_rejected(bridge_env):
    with _Bridge(lambda body: (200, {"text": "should not get here"})) as b:
        bridge_env(b.url, token="wrong")
        with pytest.raises(RuntimeError, match="401"):
            llm._call_llm("ping", backend="editor-bridge")


def test_malformed_response_is_rejected(bridge_env):
    with _Bridge(lambda body: (200, {"unexpected": True})) as b:
        bridge_env(b.url)
        with pytest.raises(RuntimeError, match="malformed"):
            llm._call_llm("ping", backend="editor-bridge")


@pytest.mark.parametrize(
    "url",
    ["", "https://127.0.0.1:9/complete", "http://example.com/complete", "file:///etc/passwd"],
)
def test_non_loopback_or_missing_url_is_refused(monkeypatch, url):
    monkeypatch.setenv(llm.ENV_BRIDGE_URL, url)
    with pytest.raises(RuntimeError):
        llm._call_llm("ping", backend="editor-bridge")


def test_unreachable_bridge_raises(bridge_env):
    with _Bridge(lambda body: (200, {"text": ""})) as b:
        url = b.url
    bridge_env(url)  # server already closed
    with pytest.raises(RuntimeError, match="unreachable"):
        llm._call_llm("ping", backend="editor-bridge")


def test_bidirectional_reasoning_runs_over_bridge(bridge_env):
    G = nx.DiGraph()
    G.add_node("login", label="login()", file_type="code", source_file="auth.py")
    G.add_node("hash_password", label="hash_password()", file_type="code",
               source_file="auth.py")
    G.add_edge("login", "hash_password", relation="calls", confidence="EXTRACTED")
    answer = {
        "answer": "login() hashes the password via hash_password().",
        "claims": [{"type": "calls", "source": "login", "target": "hash_password"}],
    }
    with _Bridge(lambda body: (200, {"text": json.dumps(answer)})) as b:
        bridge_env(b.url)
        result = reason(G, "how does login work",
                        provider=BackendLLMProvider(backend="editor-bridge"))
    assert not result.provider_errors
    assert result.draft_answer.startswith("login() hashes")
    assert result.validation.to_dict()["counts"]["supported"] == 1
    assert "GRAPH CONTEXT" in b.requests[0]["body"]["prompt"]
