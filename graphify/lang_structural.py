"""Language-aware structural checks for pre-execution validation.

Python keeps the ``ast`` module path in :mod:`graphify.preexec_validate`.
Every other officially supported language is parsed with its Tree-sitter
grammar when that grammar is installed. Names are read from syntax-tree nodes,
not from a scan of the raw string.

A symbol the graph does not contain stays ``UNKNOWN``. A syntax error is
reported as a note and is not a hallucination. A missing parser is reported
as parser-unavailable and does not invent contradictions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import networkx as nx

from graphify.graph_checks import (
    AggregateReport,
    check_calls,
    check_exists,
    check_method_of,
)

# Levels: ast | tree-sitter | parser-unavailable | unsupported
VALIDATION_LEVELS: dict[str, str] = {
    "python": "ast",
    "javascript": "tree-sitter",
    "typescript": "tree-sitter",
    "tsx": "tree-sitter",
    "go": "tree-sitter",
    "rust": "tree-sitter",
    "java": "tree-sitter",
    "c": "tree-sitter",
    "cpp": "tree-sitter",
    "ruby": "tree-sitter",
    "csharp": "tree-sitter",
    "php": "tree-sitter",
    "kotlin": "tree-sitter",
    "scala": "tree-sitter",
    "swift": "tree-sitter",
    "lua": "tree-sitter",
    "zig": "tree-sitter",
    "bash": "tree-sitter",
    "json": "tree-sitter",
    "powershell": "tree-sitter",
    "elixir": "tree-sitter",
    "objc": "tree-sitter",
    "julia": "tree-sitter",
    "verilog": "tree-sitter",
    "fortran": "tree-sitter",
    "groovy": "tree-sitter",
    "sql": "tree-sitter",
    "hcl": "tree-sitter",
}

_SUFFIXES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".cs": "csharp",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".lua": "lua",
    ".zig": "zig",
    ".sh": "bash",
    ".bash": "bash",
    ".json": "json",
    ".ps1": "powershell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".m": "objc",
    ".jl": "julia",
    ".v": "verilog",
    ".f90": "fortran",
    ".groovy": "groovy",
    ".sql": "sql",
    ".tf": "hcl",
    ".hcl": "hcl",
}

_GRAMMARS: dict[str, tuple[str, str]] = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "scala": ("tree_sitter_scala", "language"),
    "swift": ("tree_sitter_swift", "language"),
    "lua": ("tree_sitter_lua", "language"),
    "zig": ("tree_sitter_zig", "language"),
    "bash": ("tree_sitter_bash", "language"),
    "json": ("tree_sitter_json", "language"),
    "powershell": ("tree_sitter_powershell", "language"),
    "elixir": ("tree_sitter_elixir", "language"),
    "objc": ("tree_sitter_objc", "language"),
    "julia": ("tree_sitter_julia", "language"),
    "verilog": ("tree_sitter_verilog", "language"),
    "fortran": ("tree_sitter_fortran", "language"),
    "groovy": ("tree_sitter_groovy", "language"),
    "sql": ("tree_sitter_sql", "language"),
    "hcl": ("tree_sitter_hcl", "language"),
}

_CALL_TYPES = frozenset(
    {
        "call_expression",
        "call",
        "method_invocation",
        "invocation_expression",
        "function_call",
        "macro_invocation",
    }
)
_IMPORT_TYPES = frozenset(
    {
        "import_statement",
        "import_declaration",
        "import_from_statement",
        "use_declaration",
        "using_directive",
        "require_call",
    }
)
_MEMBER_TYPES = frozenset(
    {
        "member_expression",
        "attribute",
        "selector_expression",
        "field_expression",
        "navigation_expression",
        "scoped_identifier",
    }
)


@dataclass(frozen=True)
class StructuralExtract:
    language: str
    level: str
    syntax_error: bool
    notes: tuple[str, ...]
    imports: tuple[str, ...]
    simple_calls: tuple[str, ...]
    member_calls: tuple[tuple[str, str], ...]


def infer_language(file_path: str | None, fallback: str = "python") -> str:
    if not file_path:
        return fallback
    lower = file_path.lower()
    for suffix, language in sorted(_SUFFIXES.items(), key=lambda item: -len(item[0])):
        if lower.endswith(suffix):
            return language
    return "unknown" if file_path else fallback


def validation_level(language: str) -> str:
    """Documented validation level for one language name."""
    if language not in VALIDATION_LEVELS:
        return "unsupported"
    if language == "python":
        return "ast"
    if language not in _GRAMMARS:
        return "parser-unavailable"
    module_name, attr = _GRAMMARS[language]
    try:
        module = __import__(module_name, fromlist=[attr])
        getattr(module, attr)
    except Exception:
        return "parser-unavailable"
    return "tree-sitter"


def _language_object(language: str) -> Any | None:
    spec = _GRAMMARS.get(language)
    if spec is None:
        return None
    module_name, attr = spec
    try:
        from tree_sitter import Language

        module = __import__(module_name, fromlist=[attr])
        capsule = getattr(module, attr)
        factory = capsule() if callable(capsule) else capsule
        return Language(factory)
    except Exception:
        return None


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _identifier_name(node: Any, source: bytes) -> str | None:
    if node.type in {"identifier", "type_identifier", "property_identifier", "field_identifier", "name"}:
        text = _node_text(node, source)
        return text or None
    for child in node.named_children:
        found = _identifier_name(child, source)
        if found:
            return found
    return None


def _member_parts(node: Any, source: bytes) -> tuple[str, str] | None:
    object_node = node.child_by_field_name("object") or node.child_by_field_name("operand")
    property_node = (
        node.child_by_field_name("property")
        or node.child_by_field_name("attribute")
        or node.child_by_field_name("field")
        or node.child_by_field_name("name")
    )
    if object_node is None or property_node is None:
        return None
    owner = _identifier_name(object_node, source)
    method = _identifier_name(property_node, source)
    if owner and method:
        return owner, method
    return None


def _call_from_node(node: Any, source: bytes) -> tuple[str, str] | tuple[str] | None:
    function = node.child_by_field_name("function") or node.child_by_field_name("name")
    if function is None:
        function = node.named_children[0] if node.named_children else None
    if function is None:
        return None
    if function.type in _MEMBER_TYPES:
        parts = _member_parts(function, source)
        if parts:
            return parts
    name = _identifier_name(function, source)
    if name:
        return (name,)
    return None


def _import_names(node: Any, source: bytes) -> list[str]:
    names: list[str] = []
    for child in node.named_children:
        if child.type in {"string", "string_literal", "interpreted_string_literal"}:
            text = _node_text(child, source).strip("\"'`")
            if text:
                names.append(text.split("/")[-1])
        elif child.type in {"identifier", "dotted_name", "namespace_import", "scoped_identifier"}:
            text = _identifier_name(child, source)
            if text:
                names.append(text.split(".")[-1])
    if not names:
        text = _node_text(node, source)
        # The node was found by the parser; the last identifier-like token is the module.
        tokens = [tok.strip("\"'`;,(){}") for tok in text.replace("\n", " ").split()]
        for token in tokens:
            if token.isidentifier():
                names.append(token)
    return names


def extract_structural_references(code: str, language: str) -> StructuralExtract:
    """Parse ``code`` and collect imports and calls from the syntax tree."""
    level = validation_level(language)
    if level == "unsupported":
        return StructuralExtract(
            language,
            level,
            False,
            (f"language {language!r} has no structural validator",),
            (),
            (),
            (),
        )
    if level != "tree-sitter":
        return StructuralExtract(
            language,
            level,
            False,
            (f"language {language!r} is not parsed by the Tree-sitter validator",),
            (),
            (),
            (),
        )
    lang = _language_object(language)
    if lang is None:
        return StructuralExtract(
            language,
            "parser-unavailable",
            False,
            (f"Tree-sitter grammar for {language!r} is not installed; structural checks skipped",),
            (),
            (),
            (),
        )
    try:
        from tree_sitter import Parser

        parser = Parser(lang)
        source = code.encode("utf-8")
        tree = parser.parse(source)
    except Exception as exc:
        return StructuralExtract(
            language,
            "parser-unavailable",
            False,
            (f"Tree-sitter parse failed for {language!r}: {exc}",),
            (),
            (),
            (),
        )
    notes: list[str] = []
    if tree.root_node.has_error:
        notes.append(
            f"syntax error in {language} source; extracted references are partial and are not hallucinations"
        )
    imports: list[str] = []
    simple: list[str] = []
    members: list[tuple[str, str]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _IMPORT_TYPES:
            imports.extend(_import_names(node, source))
        elif node.type in _CALL_TYPES:
            found = _call_from_node(node, source)
            if found is None:
                notes.append(f"unresolved {language} call at byte {node.start_byte}")
            elif len(found) == 2:
                members.append((found[0], found[1]))
            else:
                simple.append(found[0])
        stack.extend(reversed(node.named_children))
    if not imports and not simple and not members and not notes:
        notes.append(f"parsed {language} but found no import or call nodes")
    return StructuralExtract(
        language,
        "tree-sitter",
        bool(tree.root_node.has_error),
        tuple(notes),
        tuple(dict.fromkeys(imports)),
        tuple(dict.fromkeys(simple)),
        tuple(dict.fromkeys(members)),
    )


def validate_tree_sitter_code(G: nx.Graph, code: str, language: str) -> tuple[AggregateReport, list[str]]:
    """Check Tree-sitter references against the graph.

    Missing graph evidence stays ``UNKNOWN`` because :func:`check_exists` and
    :func:`check_calls` already use that verdict. This function does not add a
    contradicted result merely because a name is absent.
    """
    extracted = extract_structural_references(code, language)
    report = AggregateReport()
    for module in extracted.imports:
        report.results.append(check_exists(G, module))
    for name in extracted.simple_calls:
        report.results.append(check_exists(G, name))
    for owner, method in extracted.member_calls:
        report.results.append(check_method_of(G, owner, method))
        report.results.append(check_calls(G, owner, method))
    return report, list(extracted.notes)
