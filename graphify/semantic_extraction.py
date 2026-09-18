"""Provider-agnostic orchestration for LLM-derived semantic code relationships.

Batches structural code nodes already present in the graph, asks a
configurable LLM provider to name the *intent* behind their relationships,
and returns validated results from :mod:`graphify.semantic_relations`.
Network/backend concerns (API keys, SDKs, model selection) are fully
delegated to :mod:`graphify.llm`; this module owns batching, prompting,
retry policy, and failure fallback so callers never see a raw exception.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import networkx as nx

from graphify.semantic_relations import (
    SEMANTIC_RELATION_TYPES,
    SemanticExtractionResult,
    parse_semantic_relations,
)

DEFAULT_BATCH_SIZE = 20
DEFAULT_MAX_TOKENS = 1500
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5


class SemanticExtractionError(RuntimeError):
    """Raised when a semantic LLM provider cannot produce a usable response."""


class SemanticLLMProvider(Protocol):
    """Minimal contract a semantic extraction backend must satisfy.

    Any object implementing ``complete`` can be passed to
    :func:`extract_semantic_relations` — production code uses
    :class:`BackendLLMProvider`, tests use lightweight fakes.
    """

    def complete(self, prompt: str, *, max_tokens: int) -> str: ...


@dataclass(frozen=True)
class BackendLLMProvider:
    """Adapts an existing graphify LLM backend (see ``graphify.llm.BACKENDS``)."""

    backend: str
    model: str | None = None

    def complete(self, prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        from graphify.llm import _call_llm

        return _call_llm(prompt, backend=self.backend, model=self.model, max_tokens=max_tokens)


@dataclass
class RetryingProvider:
    """Wraps any :class:`SemanticLLMProvider` with bounded, backed-off retry."""

    provider: SemanticLLMProvider
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    sleep: Callable[[float], None] = field(default=time.sleep)

    def complete(self, prompt: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.provider.complete(prompt, max_tokens=max_tokens)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_seconds * attempt)
        raise SemanticExtractionError(
            f"semantic provider failed after {self.max_attempts} attempts"
        ) from last_error


def default_provider(*, model: str | None = None) -> SemanticLLMProvider | None:
    """Return a retrying provider for the first configured backend, or ``None``.

    Mirrors :func:`graphify.llm.detect_backend`'s priority order. Returning
    ``None`` (instead of raising) lets callers fall back to structural-only
    graphs when no LLM credentials are configured.
    """
    from graphify.llm import detect_backend

    backend = detect_backend()
    if backend is None:
        return None
    return RetryingProvider(BackendLLMProvider(backend=backend, model=model))


@dataclass(frozen=True)
class CodeUnit:
    """A single code-graph node eligible for semantic relationship extraction."""

    node_id: str
    label: str
    source_file: str
    source_location: str | None = None


def select_code_units(G: nx.Graph, *, limit: int | None = None) -> list[CodeUnit]:
    """Collect structural code nodes in deterministic order.

    Only nodes tagged ``file_type == "code"`` participate — semantic edges
    connect real code entities, not documents/concepts/rationale nodes.
    """
    units = [
        CodeUnit(
            node_id=str(node_id),
            label=str(data.get("label", node_id)),
            source_file=str(data.get("source_file", "")),
            source_location=data.get("source_location"),
        )
        for node_id, data in G.nodes(data=True)
        if data.get("file_type") == "code"
    ]
    units.sort(key=lambda unit: (unit.source_file, unit.node_id))
    return units[:limit] if limit is not None else units


def batch_code_units(
    units: list[CodeUnit], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[CodeUnit]]:
    """Split units into deterministic, size-bounded batches for LLM calls."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [units[i : i + batch_size] for i in range(0, len(units), batch_size)]


_SEMANTIC_PROMPT_TEMPLATE = """\
You are a code-intent analyst. Given the code units below, identify semantic \
relationships that describe INTENT, not structure - how one unit's \
responsibility relates to another's.

Allowed relation types (use exactly these, lowercase): {relation_types}

Code units (id: label @ source_file:source_location):
{unit_lines}

Return ONLY valid JSON, no markdown fences, matching this shape:
{{"relations": [{{"source": "<id>", "relation": "<type>", "target": "<id-or-concept>", \
"confidence": "EXTRACTED|INFERRED|AMBIGUOUS", "rationale": "<short phrase>"}}]}}

Only report relationships you are reasonably confident about. If none apply, \
return {{"relations": []}}.
"""


def build_semantic_prompt(
    units: list[CodeUnit],
    *,
    relation_types: frozenset[str] = SEMANTIC_RELATION_TYPES,
) -> str:
    """Render the deterministic prompt for one batch of code units."""
    unit_lines = "\n".join(
        f"- {unit.node_id}: {unit.label} @ {unit.source_file}:{unit.source_location or ''}"
        for unit in units
    )
    return _SEMANTIC_PROMPT_TEMPLATE.format(
        relation_types=", ".join(sorted(relation_types)),
        unit_lines=unit_lines,
    )


def extract_semantic_relations(
    G: nx.Graph,
    *,
    provider: SemanticLLMProvider | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    limit: int | None = None,
) -> SemanticExtractionResult:
    """Run semantic extraction over a code graph's structural nodes.

    Always returns a usable :class:`SemanticExtractionResult` — never raises.
    Falls back to an empty (but non-fatal) result when no provider is
    configured, no code units exist, or every batch call fails.
    """
    active_provider = provider or default_provider()
    if active_provider is None:
        result = SemanticExtractionResult()
        result.errors.append("no semantic LLM provider configured")
        return result

    units = select_code_units(G, limit=limit)
    if not units:
        result = SemanticExtractionResult()
        result.errors.append("no code units found for semantic extraction")
        return result

    known_ids = frozenset(str(node) for node in G.nodes())
    aggregate = SemanticExtractionResult()
    for batch in batch_code_units(units, batch_size=batch_size):
        prompt = build_semantic_prompt(batch)
        try:
            raw_text = active_provider.complete(prompt, max_tokens=max_tokens)
        except SemanticExtractionError as exc:
            aggregate.errors.append(str(exc))
            continue
        batch_result = parse_semantic_relations(raw_text, known_node_ids=known_ids)
        aggregate = aggregate.merge(batch_result)
    return aggregate
