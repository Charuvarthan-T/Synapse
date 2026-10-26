"""Source-code excerpts for LLM context (graph + code, like an AI code editor).

Graph retrieval tells the model *which* symbols matter and how they connect;
this module adds *what they actually do* by reading the source of the
top-ranked symbols from disk. Excerpts are bounded by a character budget,
confined to the project root, and wrapped as untrusted data so file contents
can never act as instructions.

Symbol ranges: the graph stores only a start line. Python end lines come from
the standard-library ``ast`` module (exact); other languages end just before
the next symbol in the same file, capped at ``max_lines`` either way.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

DEFAULT_CHAR_BUDGET = 9000  # ~3000 tokens at 3 chars/token, matching serve.py's estimate
DEFAULT_MAX_EXCERPTS = 8
DEFAULT_MAX_LINES = 80
_MAX_FILE_BYTES = 2 * 1024 * 1024
_LINE_RE = re.compile(r"L(\d+)")


@dataclass(frozen=True)
class CodeExcerpt:
    """One contiguous block of source code for a graph symbol."""

    node_id: str
    label: str
    file: str  # project-relative, forward slashes
    start_line: int
    end_line: int
    text: str
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "truncated": self.truncated,
        }


def _start_line(location: object) -> int | None:
    m = _LINE_RE.search(str(location or ""))
    return int(m.group(1)) if m else None


def _is_file_node(data: dict) -> bool:
    """File nodes are labelled with the file's name/path and sit at L1."""
    src = str(data.get("source_file") or "").replace("\\", "/")
    label = str(data.get("label") or "").replace("\\", "/")
    return bool(src) and str(data.get("source_location")) == "L1" and (
        src == label or src.endswith("/" + label)
    )


def resolve_inside(root: Path, source_file: str) -> Path | None:
    """Return the absolute path for ``source_file`` only if it lies under ``root``."""
    try:
        root_resolved = root.resolve()
        candidate = Path(source_file)
        candidate = (candidate if candidate.is_absolute() else root_resolved / candidate).resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def python_symbol_ends(source: str) -> dict[int, int]:
    """Map each def/class start line (and its first decorator line) to its end line."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    ends: dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None)
            if end is None:
                continue
            ends[node.lineno] = end
            if node.decorator_list:
                ends.setdefault(node.decorator_list[0].lineno, end)
    return ends


class _FileCache:
    def __init__(self, root: Path):
        self.root = root
        self._lines: dict[str, list[str] | None] = {}
        self._py_ends: dict[str, dict[int, int]] = {}
        self._starts: dict[str, list[int]] = {}

    def lines(self, source_file: str) -> list[str] | None:
        if source_file not in self._lines:
            path = resolve_inside(self.root, source_file)
            text: str | None = None
            if path is not None:
                try:
                    if path.stat().st_size <= _MAX_FILE_BYTES:
                        text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
            self._lines[source_file] = text.splitlines() if text is not None else None
            if text is not None and source_file.endswith((".py", ".pyi")):
                self._py_ends[source_file] = python_symbol_ends(text)
        return self._lines[source_file]

    def py_ends(self, source_file: str) -> dict[int, int]:
        return self._py_ends.get(source_file, {})

    def symbol_starts(self, G: nx.Graph, source_file: str) -> list[int]:
        if source_file not in self._starts:
            starts = {
                line
                for _, d in G.nodes(data=True)
                if str(d.get("source_file") or "") == source_file
                and (line := _start_line(d.get("source_location"))) is not None
            }
            self._starts[source_file] = sorted(starts)
        return self._starts[source_file]


def _end_line(cache: _FileCache, G: nx.Graph, source_file: str, start: int, n_lines: int, max_lines: int) -> int:
    end = cache.py_ends(source_file).get(start)
    if end is None:
        later = [s for s in cache.symbol_starts(G, source_file) if s > start]
        end = (later[0] - 1) if later else n_lines
    end = min(end, n_lines, start + max_lines - 1)
    return max(end, start)


def collect_code_excerpts(
    G: nx.Graph,
    ranked_node_ids: list[str],
    root: str | Path,
    *,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    max_excerpts: int = DEFAULT_MAX_EXCERPTS,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[CodeExcerpt]:
    """Read source for the highest-ranked code symbols, within ``char_budget``.

    Skips file-level nodes, non-code nodes, files outside ``root``, and ranges
    already covered by an earlier (higher-ranked) excerpt in the same file.
    """
    root = Path(root)
    cache = _FileCache(root)
    excerpts: list[CodeExcerpt] = []
    covered: dict[str, list[tuple[int, int]]] = {}
    used = 0
    for nid in ranked_node_ids:
        if len(excerpts) >= max_excerpts or used >= char_budget:
            break
        if nid not in G:
            continue
        d = G.nodes[nid]
        source_file = str(d.get("source_file") or "")
        if not source_file or d.get("file_type", "code") != "code" or _is_file_node(d):
            continue
        start = _start_line(d.get("source_location"))
        lines = cache.lines(source_file) if start else None
        if not lines or start is None or start > len(lines):
            continue
        end = _end_line(cache, G, source_file, start, len(lines), max_lines)
        if any(a <= start and end <= b for a, b in covered.get(source_file, [])):
            continue
        natural_end = cache.py_ends(source_file).get(start, end)
        block = lines[start - 1 : end]
        while block and not block[-1].strip():
            block.pop()
            end -= 1
        if not block:
            continue
        rendered = "\n".join(f"{start + i}| {line}" for i, line in enumerate(block))
        truncated = natural_end > end
        remaining = char_budget - used
        if len(rendered) > remaining:
            if excerpts and remaining < 400:
                break
            cut = rendered[:remaining].rfind("\n")
            if cut <= 0:
                break
            rendered = rendered[:cut]
            end = start + rendered.count("\n")
            truncated = True
        excerpts.append(
            CodeExcerpt(
                node_id=str(nid),
                label=str(d.get("label") or nid),
                file=source_file.replace("\\", "/"),
                start_line=start,
                end_line=end,
                text=rendered,
                truncated=truncated,
            )
        )
        covered.setdefault(source_file, []).append((start, end))
        used += len(rendered)
    return excerpts


def render_code_context(excerpts: list[CodeExcerpt]) -> str:
    """Render excerpts as labelled untrusted-data blocks for a prompt."""
    from graphify.llm import _neutralise_injection_sentinels

    def attr(value: str) -> str:
        return _neutralise_injection_sentinels(value).replace('"', "'").replace("\n", " ")

    blocks = []
    for ex in excerpts:
        note = " truncated" if ex.truncated else ""
        blocks.append(
            f'<untrusted_source path="{attr(ex.file)}" lines="{ex.start_line}-{ex.end_line}" '
            f'symbol="{attr(ex.label)}"{note}>\n'
            f"{_neutralise_injection_sentinels(ex.text)}\n"
            "</untrusted_source>"
        )
    return "\n\n".join(blocks)
