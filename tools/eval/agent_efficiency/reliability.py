"""Offline reliability probes.

These exercise the graph CLI and the structural-validation level helper.
They are not agent reliability results.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run_query(graph: Path, question: str, repo: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("GRAPHIFY_OUT", None)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "query", question, "--graph", str(graph), "--budget", "200"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def probe_missing_graph(repo: Path, work: Path) -> dict[str, Any]:
    missing = work / "does-not-exist.json"
    proc = _run_query(missing, "where is main", repo)
    return {
        "probe": "missing_graph",
        "returncode": proc.returncode,
        "refused": proc.returncode != 0,
        "stderr_tail": proc.stderr[-300:],
    }


def probe_corrupt_graph(repo: Path, work: Path) -> dict[str, Any]:
    path = work / "corrupt.json"
    path.write_text("{truncated", encoding="utf-8")
    proc = _run_query(path, "where is main", repo)
    return {
        "probe": "corrupt_graph",
        "returncode": proc.returncode,
        "refused": proc.returncode != 0,
        "stderr_tail": proc.stderr[-300:],
    }


def probe_stale_graph(repo: Path, work: Path) -> dict[str, Any]:
    """Build a one-file graph, edit the file, and query without rebuilding."""
    from agent_efficiency.lifecycle import extract_corpus

    source = work / "stale_src"
    source.mkdir(parents=True, exist_ok=True)
    module = source / "sample.py"
    module.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    out = work / "stale_out"
    built = extract_corpus(source=source, out_dir=out, engine="synapse", upstream=work, repo=repo)
    module.write_text("def alpha():\n    return 1\n\ndef beta_added_later():\n    return 2\n", encoding="utf-8")
    graph = out / "graphify-out" / "graph.json"
    proc = _run_query(graph, "beta_added_later", repo)
    stdout = proc.stdout
    return {
        "probe": "stale_graph",
        "extract_returncode": built["returncode"],
        "query_returncode": proc.returncode,
        "new_symbol_absent_from_stale_graph": "beta_added_later" not in stdout,
        "old_symbol_still_served": "alpha" in stdout,
        "note": "The query was not followed by an incremental rebuild.",
    }


def probe_validation_levels() -> dict[str, Any]:
    from graphify.lang_structural import validation_level

    languages = ["python", "javascript", "go", "not-a-language"]
    return {
        "probe": "parser_availability",
        "levels": {language: validation_level(language) for language in languages},
        "note": "unsupported and parser-unavailable are not hallucination verdicts.",
    }


def probe_concurrent_queries(repo: Path, graph: Path, question: str) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    def once(_: int) -> int:
        return _run_query(graph, question, repo).returncode

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(once, range(4)))
    return {
        "probe": "concurrent_queries",
        "workers": 4,
        "returncodes": codes,
        "all_ok": all(code == 0 for code in codes),
    }


def probe_repeated_query(repo: Path, graph: Path, question: str) -> dict[str, Any]:
    first = _run_query(graph, question, repo)
    second = _run_query(graph, question, repo)

    def labels(text: str) -> list[str]:
        found = []
        for line in text.splitlines():
            if line.startswith("NODE "):
                found.append(line.split("[", 1)[0].replace("NODE ", "", 1).strip())
        return found

    left = labels(first.stdout)
    right = labels(second.stdout)
    return {
        "probe": "repeated_query",
        "returncodes": [first.returncode, second.returncode],
        "same_node_labels": left == right,
        "nodes": len(left),
    }


def probe_interrupted_extract(repo: Path, source: Path, work: Path) -> dict[str, Any]:
    """Kill an extract if it is still running shortly after launch.

    A corpus that finishes first is recorded as inconclusive, not as a failure
    of the extractor.
    """
    import subprocess
    import time

    out = work / "interrupt_out"
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("GRAPHIFY_OUT", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "graphify",
            "extract",
            str(source),
            "--code-only",
            "--no-cluster",
            "--out",
            str(out),
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=30)
        graph = out / "graphify-out" / "graph.json"
        valid = False
        if graph.exists():
            try:
                json.loads(graph.read_text(encoding="utf-8"))
                valid = True
            except json.JSONDecodeError:
                valid = False
        return {
            "probe": "interrupted_extract",
            "status": "interrupted",
            "returncode": proc.returncode,
            "graph_json_valid": valid if graph.exists() else None,
            "graph_exists": graph.exists(),
        }
    return {
        "probe": "interrupted_extract",
        "status": "inconclusive",
        "returncode": proc.returncode,
        "note": "The extract process exited before the interrupt. No failure is inferred.",
    }
