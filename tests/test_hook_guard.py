"""Rigorous edge-case coverage for the `graphify hook-guard` subcommand (#522).

Covers the shell-agnostic PreToolUse/BeforeTool guard that replaced the inline
bash hooks: the search/read detection matrix, the gemini BeforeTool contract,
fail-open behavior, output-dir overrides, subcommand dispatch, exit codes, and
UTF-8 (em dash) byte fidelity. Detection is exercised by calling _run_hook_guard
directly (hermetic, fast); dispatch/exit/encoding go through a real subprocess.
"""

import io
import json
import os
import subprocess
import sys

import pytest

from graphify import __main__ as m


def _invoke(kind, payload, tmp_path, monkeypatch, *, graph=True, out_name="graphify-out"):
    monkeypatch.setattr("graphify.paths.GRAPHIFY_OUT", out_name)
    monkeypatch.setattr("graphify.paths.GRAPHIFY_OUT_NAME", out_name)
    monkeypatch.chdir(tmp_path)
    if graph:
        (tmp_path / out_name).mkdir(parents=True, exist_ok=True)
        (tmp_path / out_name / "graph.json").write_text("{}", encoding="utf-8")

    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif payload is None:
        data = b""
    else:
        data = json.dumps(payload).encode("utf-8")

    class _Stdin:
        def __init__(self, b):
            self.buffer = io.BytesIO(b)

    monkeypatch.setattr(sys, "stdin", _Stdin(data))
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    m._run_hook_guard(kind)
    return buf.getvalue()


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn foo .",
        "pgrep -f server",
        "egrep pattern file",
        "fgrep lit file",
        "ls -la | grep foo",
        "ripgrep thing",
        "rg pattern src/",
        "find . -name '*.py'",
        "fd bar",
        "ack needle",
        "ag needle",
    ],
)
def test_search_nudges(command, tmp_path, monkeypatch):
    out = _invoke("search", {"tool_input": {"command": command}}, tmp_path, monkeypatch)
    assert "graphify query" in out, f"{command!r} should nudge"
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


@pytest.mark.parametrize(
    "command",
    [
        "",
        "ls -la",
        "git status",
        "cat README.md",
        "python app.py",
        "cd findings && ls",
        "manage db migrate",
        "echo hello",
    ],
)
def test_search_silent(command, tmp_path, monkeypatch):
    out = _invoke("search", {"tool_input": {"command": command}}, tmp_path, monkeypatch)
    assert out.strip() == "", f"{command!r} should be silent"


def test_search_silent_without_graph(tmp_path, monkeypatch):
    out = _invoke(
        "search", {"tool_input": {"command": "grep x"}}, tmp_path, monkeypatch, graph=False
    )
    assert out.strip() == ""


def test_search_missing_command_key(tmp_path, monkeypatch):
    out = _invoke("search", {"tool_input": {}}, tmp_path, monkeypatch)
    assert out.strip() == ""


def test_search_non_string_command_is_silent(tmp_path, monkeypatch):
    out = _invoke("search", {"tool_input": {"command": 123}}, tmp_path, monkeypatch)
    assert out.strip() == ""


def test_search_top_level_command_without_tool_input(tmp_path, monkeypatch):
    out = _invoke("search", {"command": "grep x"}, tmp_path, monkeypatch)
    assert "graphify query" in out


def test_search_non_dict_tool_input_is_silent(tmp_path, monkeypatch):
    out = _invoke("search", {"tool_input": "grep foo"}, tmp_path, monkeypatch)
    assert out.strip() == ""


@pytest.mark.parametrize(
    "tool_input",
    [
        {"file_path": "src/app.py"},
        {"file_path": "pkg/mod.ts"},
        {"file_path": "src/App.vue"},
        {"file_path": "src/Hero.astro"},
        {"file_path": "src/Card.svelte"},
        {"file_path": "SRC/APP.PY"},
        {"file_path": "src/a.test.tsx"},
        {"file_path": "lib/foo.min.js"},
        {"file_path": r"src\components\app.py"},
        {"pattern": "**/*.py", "path": "src"},
        {"pattern": "**/*.astro"},
    ],
)
def test_read_nudges(tool_input, tmp_path, monkeypatch):
    out = _invoke("read", {"tool_input": tool_input}, tmp_path, monkeypatch)
    assert "graphify query" in out, f"{tool_input!r} should nudge"


@pytest.mark.parametrize(
    "tool_input",
    [
        {"file_path": "package.json"},
        {"file_path": "tsconfig.json"},
        {"file_path": "data.geojson"},
        {"file_path": "uv.lock"},
        {"file_path": "logo.png"},
        {"file_path": "data.bin"},
        {"file_path": ".gitignore"},
        {"file_path": "Makefile"},
        {"file_path": "my.ts/file"},
        {"file_path": "graphify-out/GRAPH_REPORT.md"},
        {"file_path": ""},
        {},
    ],
)
def test_read_silent(tool_input, tmp_path, monkeypatch):
    out = _invoke("read", {"tool_input": tool_input}, tmp_path, monkeypatch)
    assert out.strip() == "", f"{tool_input!r} should be silent"


def test_read_silent_without_graph(tmp_path, monkeypatch):
    out = _invoke(
        "read", {"tool_input": {"file_path": "src/app.py"}}, tmp_path, monkeypatch, graph=False
    )
    assert out.strip() == ""


def test_read_non_dict_tool_input_is_silent(tmp_path, monkeypatch):
    out = _invoke("read", {"tool_input": ["src/app.py"]}, tmp_path, monkeypatch)
    assert out.strip() == ""


def test_read_respects_custom_output_dir_name(tmp_path, monkeypatch):
    out = _invoke(
        "read",
        {"tool_input": {"file_path": "build-out/report.py"}},
        tmp_path,
        monkeypatch,
        graph=True,
        out_name="build-out",
    )
    assert out.strip() == ""


def test_read_nudges_source_outside_custom_output_dir(tmp_path, monkeypatch):
    out = _invoke(
        "read",
        {"tool_input": {"file_path": "src/app.py"}},
        tmp_path,
        monkeypatch,
        graph=True,
        out_name="build-out",
    )
    assert "graphify query" in out


@pytest.mark.parametrize("kind", ["search", "read"])
@pytest.mark.parametrize("raw", [b"not json at all", b"", b"[1,2,3]", b"\xff\xfe\x00bad"])
def test_fail_open_on_bad_stdin(kind, raw, tmp_path, monkeypatch):
    out = _invoke(kind, raw, tmp_path, monkeypatch)
    assert out.strip() == ""


def test_search_out_path_error_is_swallowed(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr("graphify.paths.out_path", _boom)
    out = _invoke("search", {"tool_input": {"command": "grep x"}}, tmp_path, monkeypatch)
    assert out.strip() == ""


def test_gemini_allow_with_nudge(tmp_path, monkeypatch):
    out = _invoke("gemini", None, tmp_path, monkeypatch, graph=True)
    payload = json.loads(out)
    assert payload["decision"] == "allow"
    assert "graphify query" in payload["additionalContext"]


def test_gemini_allow_without_graph(tmp_path, monkeypatch):
    out = _invoke("gemini", None, tmp_path, monkeypatch, graph=False)
    payload = json.loads(out)
    assert payload == {"decision": "allow"}


def test_gemini_always_allows_even_when_check_throws(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr("graphify.paths.out_path", _boom)
    out = _invoke("gemini", None, tmp_path, monkeypatch, graph=True)
    assert json.loads(out) == {"decision": "allow"}


def _env():
    e = dict(os.environ)
    e.pop("GRAPHIFY_OUT", None)
    return e


def _cli(args, tmp_path, stdin=""):
    return subprocess.run(
        [sys.executable, "-m", "graphify", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_env(),
    )


def test_dispatch_missing_mode_exits_zero_silent(tmp_path):
    r = _cli(["hook-guard"], tmp_path, stdin="{}")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_dispatch_unknown_mode_exits_zero_silent(tmp_path):
    r = _cli(["hook-guard", "bogus"], tmp_path, stdin="{}")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@pytest.mark.parametrize(
    "args,stdin",
    [
        (["hook-guard", "search"], '{"tool_input":{"command":"grep x"}}'),
        (["hook-guard", "read"], '{"tool_input":{"file_path":"a.py"}}'),
        (["hook-guard", "gemini"], ""),
    ],
)
def test_dispatch_always_exits_zero(args, stdin, tmp_path):
    (tmp_path / "graphify-out").mkdir()
    (tmp_path / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    r = _cli(args, tmp_path, stdin=stdin)
    assert r.returncode == 0


def test_read_nudge_em_dash_survives_utf8(tmp_path):
    (tmp_path / "graphify-out").mkdir()
    (tmp_path / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "graphify", "hook-guard", "read"],
        input=b'{"tool_input":{"file_path":"src/app.py"}}',
        capture_output=True,
        cwd=tmp_path,
        env=_env(),
    )
    assert r.returncode == 0
    text = r.stdout.decode("utf-8")
    payload = json.loads(text)
    assert "—" in payload["hookSpecificOutput"]["additionalContext"]
