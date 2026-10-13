"""Graph lifecycle measurements that are not agent results.

Indexing, update, and query timings come from the real CLI. API cost is left
null because these commands do not call a model.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from graphify.paths import GRAPHIFY_OUT


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _env_for(engine: str, upstream: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GRAPHIFY_NO_AUTO_REFRESH"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("GRAPHIFY_OUT", None)
    if engine == "upstream":
        env["PYTHONPATH"] = str(upstream)
    return env


def _cwd_for(engine: str, upstream: Path, repo: Path) -> Path:
    return upstream if engine == "upstream" else repo


def extract_corpus(
    *,
    source: Path,
    out_dir: Path,
    engine: str,
    upstream: Path,
    repo: Path,
    fresh: bool = True,
) -> dict[str, Any]:
    """Run ``graphify extract --code-only --no-cluster`` and record the result.

    ``fresh=False`` keeps an existing graph so the CLI can take its incremental path.
    """
    if fresh and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "graphify",
        "extract",
        str(source),
        "--code-only",
        "--no-cluster",
        "--out",
        str(out_dir),
    ]
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=_cwd_for(engine, upstream, repo),
        env=_env_for(engine, upstream),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    peak_holder: dict[str, int | None] = {"rss": None}
    sampler = threading.Thread(target=_sample_rss, args=(proc, peak_holder), daemon=True)
    sampler.start()
    _stdout, stderr = proc.communicate()
    sampler.join(timeout=2)
    elapsed = time.perf_counter() - started
    peak_rss = peak_holder["rss"]
    graph_path = out_dir / GRAPHIFY_OUT / "graph.json"
    nodes = edges = None
    graph_bytes = None
    if graph_path.exists():
        graph_bytes = graph_path.stat().st_size
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
            nodes = len(payload.get("nodes") or [])
            edges = len(payload.get("links") or payload.get("edges") or [])
        except json.JSONDecodeError:
            nodes = None
            edges = None
    return {
        "engine": engine,
        "source": source.name,
        "files": len(list(source.rglob("*.py"))),
        "returncode": proc.returncode,
        "wall_clock_s": elapsed,
        "graph_bytes": graph_bytes,
        "nodes": nodes,
        "edges": edges,
        "api_cost_usd": None,
        "api_cost_reason": "extract --code-only does not call a model",
        "memory_rss_peak_bytes": peak_rss,
        "memory_note": None if peak_rss is not None else "psutil is not installed; RSS was not sampled",
        "stderr_tail": (stderr or "")[-500:],
    }


def _sample_rss(proc: subprocess.Popen[str], holder: dict[str, int | None]) -> None:
    try:
        import psutil
    except ImportError:
        return
    try:
        process = psutil.Process(proc.pid)
    except psutil.Error:
        return
    peak = 0
    while proc.poll() is None:
        try:
            peak = max(peak, int(process.memory_info().rss))
        except psutil.Error:
            break
        time.sleep(0.05)
    if peak:
        holder["rss"] = peak


def query_latency(
    *,
    question: str,
    graph: Path,
    engine: str,
    upstream: Path,
    repo: Path,
    flags: list[str],
    repeats: int,
    warmup: int,
) -> dict[str, Any]:
    samples: list[float] = []
    returncodes: list[int] = []
    cmd = [
        sys.executable,
        "-m",
        "graphify",
        "query",
        question,
        "--graph",
        str(graph),
        "--budget",
        "2000",
        *flags,
    ]
    for index in range(warmup + repeats):
        started = time.perf_counter()
        proc = subprocess.run(
            cmd,
            cwd=_cwd_for(engine, upstream, repo),
            env=_env_for(engine, upstream),
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - started
        if index >= warmup:
            samples.append(elapsed)
            returncodes.append(proc.returncode)
    return {
        "engine": engine,
        "flags": flags,
        "warmup_discarded": warmup,
        "repeats": repeats,
        "returncodes": returncodes,
        "latency_p50_s": statistics.median(samples) if samples else None,
        "latency_p95_s": _percentile(samples, 0.95),
        "latency_samples_s": samples,
        "includes_process_startup": True,
        "api_cost_usd": None,
    }


def copy_python_files(files: list[Path], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for path in files:
        target = dest / path.name
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
