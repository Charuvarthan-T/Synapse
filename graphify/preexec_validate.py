"""Graph-based pre-execution hallucination detection (Synapse contribution C3).

Validates proposed agent code / tool payloads against the repository knowledge
graph **before** execution. Missing graph evidence yields ``UNKNOWN`` (do not
reject); explicit contradictions yield ``INVALID``.

Integration points:
  - CLI: ``graphify preexec-check``
  - PreToolUse: ``graphify hook-guard write`` (opt-in via env / install flag)
  - MCP: ``validate_proposal`` tool
  - Library: :func:`validate_proposal`
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from graphify.graph_checks import (
    AggregateReport,
    CheckResult,
    Verdict,
    check_calls,
    check_exists,
    check_imports,
    check_method_of,
)

# Opt-in gates — default off so existing agent behaviour is unchanged.
ENV_PREEXEC = "GRAPHIFY_PREEXEC"
ENV_PREEXEC_STRICT = "GRAPHIFY_PREEXEC_STRICT"


@dataclass
class ProposedAction:
    """Normalised agent proposal prior to execution."""

    kind: str  # code | tool_call | write | edit | unknown
    language: str = "python"
    content: str = ""
    file_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreExecReport:
    """Structured pre-execution validation outcome."""

    action: ProposedAction
    checks: AggregateReport = field(default_factory=AggregateReport)
    blocked: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.checks.status

    @property
    def verdict(self) -> Verdict:
        return self.checks.verdict

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "verdict": self.verdict.value,
            "blocked": self.blocked,
            "action": {
                "kind": self.action.kind,
                "language": self.action.language,
                "file_path": self.action.file_path,
            },
            "checks": self.checks.to_dict(),
            "notes": list(self.notes),
        }

    def format_message(self) -> str:
        lines = [
            f"Pre-execution graph check: {self.status}",
            f"overall_verdict={self.verdict.value} blocked={self.blocked}",
        ]
        for item in self.checks.results:
            lines.append(f"- [{item.status}] {item.claim}: {item.reason}")
            for ev in item.evidence:
                lines.append(f"    evidence: {ev.detail}")
        for note in self.notes:
            lines.append(f"- note: {note}")
        return "\n".join(lines)


_IMPORT_FROM = re.compile(
    r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)",
    re.MULTILINE,
)
_IMPORT = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)", re.MULTILINE)
_CALL = re.compile(
    r"\b([A-Za-z_][\w]*)\s*\.\s*([A-Za-z_][\w]*)\s*\(",
)
_SIMPLE_CALL = re.compile(r"(?<!\.)\b([A-Za-z_][\w]*)\s*\(")


def _split_names(blob: str) -> list[str]:
    parts: list[str] = []
    for piece in blob.split(","):
        name = piece.strip()
        if " as " in name:
            name = name.split(" as ", 1)[0].strip()
        if name and name != "*":
            parts.append(name)
    return parts


def extract_python_references(code: str) -> dict[str, list[tuple[str, ...]]]:
    """Extract imports, attribute calls, and simple calls from Python source.

    Prefer AST; fall back to regexes when the snippet is incomplete.
    """
    imports: list[tuple[str, ...]] = []
    attr_calls: list[tuple[str, ...]] = []
    simple_calls: list[tuple[str, ...]] = []
    methods: list[tuple[str, ...]] = []

    tree: ast.AST | None = None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(("import", alias.name))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        imports.append(("import", mod))
                    else:
                        imports.append(("from_import", mod, alias.name))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    attr_calls.append(("attr_call", func.value.id, func.attr))
                    methods.append(("method_of", func.value.id, func.attr))
                elif isinstance(func, ast.Name):
                    if func.id not in {"print", "len", "str", "int", "float", "list", "dict", "set", "range", "open", "type", "isinstance", "super", "property", "staticmethod", "classmethod"}:
                        simple_calls.append(("call", func.id))
        return {
            "imports": imports,
            "attr_calls": attr_calls,
            "simple_calls": simple_calls,
            "methods": methods,
        }

    # Regex fallback for partial snippets.
    for match in _IMPORT_FROM.finditer(code):
        mod = match.group(1)
        for name in _split_names(match.group(2)):
            imports.append(("from_import", mod, name))
    for match in _IMPORT.finditer(code):
        for name in _split_names(match.group(1)):
            imports.append(("import", name))
    for match in _CALL.finditer(code):
        attr_calls.append(("attr_call", match.group(1), match.group(2)))
        methods.append(("method_of", match.group(1), match.group(2)))
    for match in _SIMPLE_CALL.finditer(code):
        simple_calls.append(("call", match.group(1)))
    return {
        "imports": imports,
        "attr_calls": attr_calls,
        "simple_calls": simple_calls,
        "methods": methods,
    }


def proposal_from_tool_input(tool_name: str | None, tool_input: dict[str, Any]) -> ProposedAction:
    """Build a :class:`ProposedAction` from a PreToolUse tool_input payload."""
    name = (tool_name or "").strip()
    content = ""
    path = None
    kind = "tool_call"

    if name in {"Write", "write"}:
        kind = "write"
        content = str(tool_input.get("content") or tool_input.get("new_string") or "")
        path = tool_input.get("file_path") or tool_input.get("path")
    elif name in {"Edit", "edit", "StrReplace", "MultiEdit"}:
        kind = "edit"
        content = str(
            tool_input.get("new_string")
            or tool_input.get("content")
            or tool_input.get("new_str")
            or ""
        )
        path = tool_input.get("file_path") or tool_input.get("path")
    elif name in {"Bash", "bash", "Shell"}:
        kind = "tool_call"
        content = str(tool_input.get("command") or "")
    else:
        content = str(
            tool_input.get("content")
            or tool_input.get("new_string")
            or tool_input.get("code")
            or tool_input.get("command")
            or ""
        )
        path = tool_input.get("file_path") or tool_input.get("path")

    language = "python"
    if path and str(path).endswith((".ts", ".tsx", ".js", ".jsx")):
        language = "javascript"
    elif path and str(path).endswith((".py",)):
        language = "python"

    return ProposedAction(
        kind=kind,
        language=language,
        content=content,
        file_path=str(path) if path else None,
        metadata={"tool_name": name},
    )


def _validate_python_action(G: nx.Graph, action: ProposedAction) -> AggregateReport:
    refs = extract_python_references(action.content)
    report = AggregateReport()

    for item in refs["imports"]:
        if item[0] == "import":
            report.results.append(check_exists(G, item[1]))
            # Also try top-level package.
            top = item[1].split(".", 1)[0]
            if top != item[1]:
                # Don't double-count noise: only add if primary unknown.
                if report.results[-1].verdict is Verdict.UNKNOWN:
                    report.results.append(check_exists(G, top))
        elif item[0] == "from_import":
            mod, name = item[1], item[2]
            mod_check = check_exists(G, mod)
            report.results.append(mod_check)
            # Prefer import edge when both sides known; else exists on symbol.
            if mod_check.verdict is Verdict.SUPPORTED:
                edge = check_imports(G, mod, name)
                if edge.verdict is Verdict.SUPPORTED:
                    report.results.append(edge)
                else:
                    report.results.append(check_exists(G, f"{mod}.{name}"))
                    report.results.append(check_exists(G, name))
            else:
                report.results.append(check_exists(G, name))

    for item in refs["methods"]:
        report.results.append(check_method_of(G, item[1], item[2]))

    for item in refs["attr_calls"]:
        # Attribute call A.b() — also try calls edge if receiver looks like a function/class node.
        report.results.append(check_calls(G, item[1], item[2]))

    for item in refs["simple_calls"]:
        report.results.append(check_exists(G, item[1]))

    return report


def validate_proposal(
    G: nx.Graph,
    action: ProposedAction,
    *,
    strict: bool = False,
) -> PreExecReport:
    """Validate a proposed action against ``G``.

    ``strict=True`` blocks (``blocked=True``) only on ``INVALID`` /
    ``CONTRADICTED``. ``UNKNOWN`` never blocks.
    """
    notes: list[str] = []
    if not action.content.strip():
        notes.append("empty proposal content — nothing to validate")
        return PreExecReport(action=action, notes=notes)

    if action.language != "python":
        notes.append(
            f"language {action.language!r} uses existence heuristics only; "
            "full AST checks are Python-first"
        )
        # Still run regex-based python extractor — often works for JS-like calls too.
        checks = _validate_python_action(G, action)
    else:
        checks = _validate_python_action(G, action)

    if not checks.results:
        notes.append("no symbol/import/call references extracted from proposal")

    blocked = bool(strict and checks.verdict is Verdict.CONTRADICTED)
    return PreExecReport(action=action, checks=checks, blocked=blocked, notes=notes)


def validate_code(
    G: nx.Graph,
    code: str,
    *,
    file_path: str | None = None,
    strict: bool = False,
) -> PreExecReport:
    action = ProposedAction(kind="code", language="python", content=code, file_path=file_path)
    return validate_proposal(G, action, strict=strict)


def preexec_enabled() -> bool:
    return os.environ.get(ENV_PREEXEC, "").strip().lower() in {"1", "true", "yes", "on"}


def preexec_strict_enabled(cli_strict: bool = False) -> bool:
    env = os.environ.get(ENV_PREEXEC_STRICT, "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    return bool(cli_strict)


def format_hook_nudge(report: PreExecReport) -> str:
    """Claude-style PreToolUse additionalContext / decision payload body."""
    return (
        "Synapse pre-execution graph validation:\n"
        + report.format_message()
        + "\nUNKNOWN means the graph lacks evidence — do not treat it as proof of invalidity. "
        "Fix CONTRADICTED/INVALID references before executing."
    )


def hook_decision_payload(report: PreExecReport, *, kind: str = "claude") -> dict[str, Any]:
    """Build hook stdout JSON for Claude or Gemini styles."""
    message = format_hook_nudge(report)
    if kind == "gemini":
        decision = "block" if report.blocked else "allow"
        payload: dict[str, Any] = {"decision": decision, "additionalContext": message}
        return payload

    # Claude Code hookSpecificOutput shape (mirrors existing nudge helpers).
    if report.blocked:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }
