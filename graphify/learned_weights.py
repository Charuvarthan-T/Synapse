"""Learn relation importance from explicit query-log feedback.

The fixed table in :mod:`graphify.weights` stays the prior and the fallback.
Learning never reads benchmark labels, ground-truth files, or records marked
held-out. A relation keeps its prior until it has been seen at least
``min_samples`` times.

Update rule
-----------
Each accepted feedback record contributes one exposure per listed relation.
``useful: true`` also contributes one positive. The stored importance is the
mean of a Beta posterior whose prior mean is the fixed-table importance and
whose strength is ``prior_strength`` (pseudo-counts):

    alpha = prior * prior_strength + positives
    beta  = (1 - prior) * prior_strength + (exposures - positives)
    importance = clamp(alpha / (alpha + beta), 0.05, 1.0)

The same log and the same prior always produce the same file. There is no
randomness and no use of retrieval ground truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from graphify.weights import (
    DEFAULT_RELATION_IMPORTANCE,
    RelationWeightRegistry,
    get_default_registry,
)

FORMAT_VERSION = 1
DEFAULT_PRIOR_STRENGTH = 8.0
DEFAULT_MIN_SAMPLES = 3
_MIN_IMPORTANCE = 0.05
_MAX_IMPORTANCE = 1.0
_HELD_OUT_SOURCES = frozenset(
    {"benchmark", "eval", "held_out", "crosscodeeval", "ground_truth", "retrieval_eval"}
)


def weights_path_for_graph(graph_path: str | Path) -> Path:
    """Versioned learned-weight file stored beside ``graph.json``."""
    return Path(graph_path).resolve().parent / "learned_weights.json"


def _clamp(value: float) -> float:
    return max(_MIN_IMPORTANCE, min(_MAX_IMPORTANCE, float(value)))


def _prior(relation: str) -> float:
    return float(DEFAULT_RELATION_IMPORTANCE.get(relation, get_default_registry().unknown_importance))


def posterior_importance(
    relation: str,
    positives: float,
    exposures: float,
    *,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
) -> float:
    """Beta-binomial posterior mean, shrunk toward the fixed-table prior."""
    prior = min(_MAX_IMPORTANCE, max(_MIN_IMPORTANCE, _prior(relation)))
    strength = max(float(prior_strength), 1e-6)
    alpha = prior * strength + float(positives)
    beta = (1.0 - prior) * strength + max(float(exposures) - float(positives), 0.0)
    return _clamp(alpha / (alpha + beta))


def _is_held_out(record: Mapping[str, Any]) -> bool:
    if record.get("held_out") is True:
        return True
    source = str(record.get("source") or "").strip().lower()
    return source in _HELD_OUT_SOURCES


def _iter_feedback(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            rec = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def learn_from_feedback(
    records: list[Mapping[str, Any]],
    *,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Fold feedback records into a versioned weight document.

    Records without a boolean ``useful`` field, without a ``relations`` list,
    or marked held-out / benchmark are counted and ignored.
    """
    positives: dict[str, float] = {}
    exposures: dict[str, float] = {}
    used = 0
    excluded = 0
    for record in records:
        if _is_held_out(record) or "useful" not in record:
            excluded += 1
            continue
        relations = record.get("relations")
        if not isinstance(relations, list) or not relations:
            excluded += 1
            continue
        useful = bool(record.get("useful"))
        seen: set[str] = set()
        for raw in relations:
            name = str(raw).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            exposures[name] = exposures.get(name, 0.0) + 1.0
            if useful:
                positives[name] = positives.get(name, 0.0) + 1.0
        used += 1

    relations_out: dict[str, Any] = {}
    for name, exposure in sorted(exposures.items()):
        pos = positives.get(name, 0.0)
        relations_out[name] = {
            "positives": pos,
            "exposures": exposure,
            "samples": int(exposure),
            "importance": posterior_importance(
                name, pos, exposure, prior_strength=prior_strength
            ),
            "active": exposure >= min_samples,
        }
    return {
        "format_version": FORMAT_VERSION,
        "prior_strength": prior_strength,
        "min_samples": int(min_samples),
        "objective": "beta_posterior_mean_of_useful_relation_feedback",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records_used": used,
        "records_excluded": excluded,
        "relations": relations_out,
    }


def learn_from_log(
    log_path: str | Path,
    out_path: str | Path,
    *,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Read a JSONL feedback log and write ``learned_weights.json``."""
    state = learn_from_feedback(
        _iter_feedback(Path(log_path)),
        prior_strength=prior_strength,
        min_samples=min_samples,
    )
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def registry_from_state(state: Mapping[str, Any]) -> RelationWeightRegistry:
    """Apply relations that have reached ``min_samples``. Others keep the prior."""
    registry = RelationWeightRegistry()
    min_samples = int(state.get("min_samples", DEFAULT_MIN_SAMPLES))
    relations = state.get("relations") or {}
    if not isinstance(relations, Mapping):
        return registry
    for name, row in relations.items():
        if not isinstance(row, Mapping):
            continue
        samples = float(row.get("exposures", row.get("samples", 0)) or 0)
        if samples < min_samples:
            continue
        importance = row.get("importance")
        if importance is None:
            continue
        registry.register(str(name), float(importance))
    return registry


def load_learned_registry(path: str | Path) -> RelationWeightRegistry | None:
    """Load a learned registry, or ``None`` when the file is missing or invalid."""
    src = Path(path)
    if not src.exists():
        return None
    try:
        state = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("format_version") != FORMAT_VERSION:
        return None
    return registry_from_state(state)


def load_registry_for_graph(graph_path: str | Path) -> RelationWeightRegistry | None:
    """Production loader. Missing file means the fixed-weight fallback."""
    return load_learned_registry(weights_path_for_graph(graph_path))
