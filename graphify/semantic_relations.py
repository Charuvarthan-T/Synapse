"""Deterministic schema for LLM-derived semantic code relationships.

Structural edges (``calls``, ``imports``, ``inherits``, ...) already exist on
the code graph. This module defines the parallel *semantic* relation
vocabulary — intent-level relationships such as ``handles`` or ``validates``
— and the parsing/validation that turns a raw LLM response into typed,
storage-ready :class:`SemanticRelation` objects. Raw LLM text is never stored
directly; only relations that pass validation here reach the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graphify.ids import normalize_id
from graphify.llm import _parse_llm_json
from graphify.validate import VALID_CONFIDENCES

SEMANTIC_RELATION_TYPES: frozenset[str] = frozenset(
    {"handles", "validates", "manages", "responsible_for", "related_to"}
)

DEFAULT_SEMANTIC_CONFIDENCE = "INFERRED"
SEMANTIC_ORIGIN = "semantic_code"
_MAX_RELATIONS_PER_RESPONSE = 200
_MAX_RATIONALE_CHARS = 280


@dataclass(frozen=True)
class SemanticRelation:
    """One validated, storage-ready semantic edge between two code units."""

    source: str
    relation: str
    target: str
    confidence: str = DEFAULT_SEMANTIC_CONFIDENCE
    rationale: str | None = None

    def to_edge_attrs(self, *, source_file: str = "") -> dict:
        """Render as an edge attribute dict compatible with the code graph schema."""
        attrs = {
            "relation": self.relation,
            "confidence": self.confidence,
            "source_file": source_file,
            "_origin": SEMANTIC_ORIGIN,
        }
        if self.rationale:
            attrs["context"] = self.rationale
        return attrs


@dataclass
class SemanticExtractionResult:
    """Aggregate, storage-ready outcome of one or more semantic extraction calls."""

    relations: list[SemanticRelation] = field(default_factory=list)
    concept_nodes: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.relations

    def merge(self, other: SemanticExtractionResult) -> SemanticExtractionResult:
        """Combine two results, deduplicating concept nodes by id."""
        seen_concepts = {node["id"] for node in self.concept_nodes}
        merged_concepts = list(self.concept_nodes)
        for node in other.concept_nodes:
            if node["id"] not in seen_concepts:
                seen_concepts.add(node["id"])
                merged_concepts.append(node)
        return SemanticExtractionResult(
            relations=self.relations + other.relations,
            concept_nodes=merged_concepts,
            errors=self.errors + other.errors,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


def _normalize_confidence(raw: object) -> str:
    value = str(raw).strip().upper() if raw else ""
    return value if value in VALID_CONFIDENCES else DEFAULT_SEMANTIC_CONFIDENCE


def _resolve_endpoint(
    raw_value: str,
    *,
    known_ids: frozenset[str],
    concept_nodes_by_id: dict[str, dict],
) -> str:
    """Map a raw LLM-provided endpoint string to a graph node id.

    Exact and normalized matches against ``known_ids`` are preferred so
    semantic edges attach to existing code nodes. An endpoint with no match
    becomes a lightweight ``concept`` node instead of a dangling reference.
    """
    candidate = raw_value.strip()
    if candidate in known_ids:
        return candidate
    normalized = normalize_id(candidate)
    if normalized in known_ids:
        return normalized
    concept_id = f"concept_{normalized}" if normalized else "concept_unknown"
    if concept_id not in concept_nodes_by_id:
        concept_nodes_by_id[concept_id] = {
            "id": concept_id,
            "label": candidate,
            "file_type": "concept",
            "source_file": "",
        }
    return concept_id


def parse_semantic_relations(
    raw_text: str,
    *,
    known_node_ids: frozenset[str] = frozenset(),
) -> SemanticExtractionResult:
    """Parse and validate an LLM response into a :class:`SemanticExtractionResult`.

    Malformed JSON, a missing/non-list ``relations`` key, or entries with
    unsupported relation types or missing endpoints are dropped with an
    explanation recorded in ``errors`` — never raised — so extraction
    failures degrade to "no semantic edges" instead of breaking the pipeline.
    """
    parsed = _parse_llm_json(raw_text)
    raw_relations = parsed.get("relations")
    result = SemanticExtractionResult(
        input_tokens=int(parsed.get("input_tokens", 0) or 0),
        output_tokens=int(parsed.get("output_tokens", 0) or 0),
    )
    if not isinstance(raw_relations, list):
        result.errors.append("response missing a 'relations' list")
        return result

    concept_nodes_by_id: dict[str, dict] = {}
    for index, entry in enumerate(raw_relations[:_MAX_RELATIONS_PER_RESPONSE]):
        if not isinstance(entry, dict):
            result.errors.append(f"relation {index} is not an object")
            continue
        source_raw = entry.get("source")
        target_raw = entry.get("target")
        relation_raw = entry.get("relation")
        if not isinstance(source_raw, str) or not source_raw.strip():
            result.errors.append(f"relation {index} missing 'source'")
            continue
        if not isinstance(target_raw, str) or not target_raw.strip():
            result.errors.append(f"relation {index} missing 'target'")
            continue
        relation = str(relation_raw).strip().lower() if relation_raw else ""
        if relation not in SEMANTIC_RELATION_TYPES:
            result.errors.append(f"relation {index} has unsupported type {relation_raw!r}")
            continue
        source_id = _resolve_endpoint(
            source_raw, known_ids=known_node_ids, concept_nodes_by_id=concept_nodes_by_id
        )
        target_id = _resolve_endpoint(
            target_raw, known_ids=known_node_ids, concept_nodes_by_id=concept_nodes_by_id
        )
        if source_id == target_id:
            result.errors.append(f"relation {index} resolves to a self-loop, skipped")
            continue
        rationale = entry.get("rationale")
        result.relations.append(
            SemanticRelation(
                source=source_id,
                relation=relation,
                target=target_id,
                confidence=_normalize_confidence(entry.get("confidence")),
                rationale=(
                    str(rationale).strip()[:_MAX_RATIONALE_CHARS]
                    if isinstance(rationale, str) and rationale.strip()
                    else None
                ),
            )
        )
    result.concept_nodes = list(concept_nodes_by_id.values())
    return result
