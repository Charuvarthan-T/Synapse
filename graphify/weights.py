"""Centralized relationship-importance weights for retrieval.

Edge costs used by traversal are derived as ``1 / importance`` so Dijkstra /
priority expansion prefer semantically stronger relations (e.g. ``calls``)
over weaker ones (e.g. ``imports``). Callers should not hardcode relation
weights elsewhere — register overrides on a :class:`RelationWeightRegistry`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, MutableMapping

# Floor used when converting importance → traversal cost.
_MIN_IMPORTANCE = 1e-6

# Default importance when relation is missing or unknown.
DEFAULT_UNKNOWN_IMPORTANCE = 0.5

# Higher = more meaningful for code-intelligence retrieval ranking/traversal.
# Ordered intentionally: structural call/type edges beat import/doc similarity.
DEFAULT_RELATION_IMPORTANCE: dict[str, float] = {
    "calls": 1.0,
    "indirect_call": 0.95,
    "inherits": 0.9,
    "extends": 0.9,
    "implements": 0.85,
    "overrides": 0.85,
    "method": 0.8,
    "contains": 0.75,
    "uses": 0.7,
    "mixes_in": 0.7,
    "embeds": 0.7,
    "bound_to": 0.65,
    "instantiates": 0.65,
    "uses_component": 0.65,
    "binds_method": 0.65,
    "references": 0.6,
    "references_constant": 0.55,
    "uses_static_prop": 0.55,
    "listened_by": 0.55,
    "defines": 0.55,
    "exports": 0.5,
    "re_exports": 0.5,
    "imports_from": 0.45,
    "imports": 0.4,
    "depends_on": 0.4,
    "crate_depends_on": 0.4,
    "includes": 0.35,
    "cites": 0.35,
    "rationale_for": 0.3,
    "conceptually_related_to": 0.25,
    "shares_data_with": 0.25,
    "participate_in": 0.25,
    "form": 0.25,
    "semantically_similar_to": 0.2,
    # Semantic (LLM-derived intent) relations — see graphify.semantic_relations.
    "handles": 0.65,
    "manages": 0.65,
    "responsible_for": 0.6,
    "validates": 0.55,
    "related_to": 0.3,
}

# Soften inferred/ambiguous edges relative to EXTRACTED.
DEFAULT_CONFIDENCE_MULTIPLIER: dict[str, float] = {
    "EXTRACTED": 1.0,
    "INFERRED": 0.85,
    "AMBIGUOUS": 0.7,
}


def importance_to_cost(importance: float) -> float:
    """Convert importance (higher better) to traversal cost (lower better)."""
    return 1.0 / max(float(importance), _MIN_IMPORTANCE)


@dataclass
class RelationWeightRegistry:
    """Lookup table for relation importance and derived traversal costs.

    Extending the scheme is a single ``register()`` call — no call-site edits.
    """

    importance_by_relation: MutableMapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_RELATION_IMPORTANCE)
    )
    unknown_importance: float = DEFAULT_UNKNOWN_IMPORTANCE
    confidence_multiplier: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CONFIDENCE_MULTIPLIER)
    )

    def register(self, relation: str, importance: float) -> None:
        """Add or overwrite a relation weight. ``importance`` must be > 0."""
        key = str(relation).strip()
        if not key:
            raise ValueError("relation name must be non-empty")
        value = float(importance)
        if value <= 0:
            raise ValueError(f"importance must be > 0, got {importance!r}")
        self.importance_by_relation[key] = value

    def register_many(self, mapping: Mapping[str, float]) -> None:
        for relation, importance in mapping.items():
            self.register(relation, importance)

    def importance(self, relation: str | None) -> float:
        if relation is None:
            return float(self.unknown_importance)
        key = str(relation).strip()
        if not key:
            return float(self.unknown_importance)
        return float(self.importance_by_relation.get(key, self.unknown_importance))

    def traversal_cost(self, relation: str | None) -> float:
        return importance_to_cost(self.importance(relation))

    def edge_importance(self, edata: Mapping | None) -> float:
        """Importance for one edge attribute dict (missing metadata → defaults).

        When the edge carries ``secondary_relations`` — semantic annotations
        layered onto a structural edge by :mod:`graphify.semantic_graph` — the
        strongest of the primary and secondary relations wins, so a `handles`
        annotation on a weak `imports` edge can still surface during ranking.
        """
        if not edata:
            return float(self.unknown_importance)

        override = edata.get("relation_weight")
        if override is not None:
            try:
                base = float(override)
                if base > 0:
                    return max(base, _MIN_IMPORTANCE)
            except (TypeError, ValueError):
                pass

        raw_rel = edata.get("relation")
        if raw_rel is None:
            raw_rel = edata.get("type")
        relation = str(raw_rel) if raw_rel is not None else None
        base = self.importance(relation)

        conf = edata.get("confidence")
        if conf is not None:
            mult = float(self.confidence_multiplier.get(str(conf), 1.0))
            base *= mult

        secondary = edata.get("secondary_relations")
        if isinstance(secondary, list):
            for item in secondary:
                if not isinstance(item, Mapping):
                    continue
                candidate = self.importance(item.get("relation"))
                item_conf = item.get("confidence")
                if item_conf is not None:
                    candidate *= float(self.confidence_multiplier.get(str(item_conf), 1.0))
                base = max(base, candidate)

        return max(base, _MIN_IMPORTANCE)

    def edge_cost(self, edata: Mapping | None) -> float:
        return importance_to_cost(self.edge_importance(edata))

    def copy(self) -> RelationWeightRegistry:
        return RelationWeightRegistry(
            importance_by_relation=dict(self.importance_by_relation),
            unknown_importance=float(self.unknown_importance),
            confidence_multiplier=dict(self.confidence_multiplier),
        )


_DEFAULT_REGISTRY = RelationWeightRegistry()


def get_default_registry() -> RelationWeightRegistry:
    """Process-wide default registry (mutable; prefer ``.copy()`` for overrides)."""
    return _DEFAULT_REGISTRY


def relation_importance(
    relation: str | None,
    registry: RelationWeightRegistry | None = None,
) -> float:
    return (registry or _DEFAULT_REGISTRY).importance(relation)


def relation_cost(
    relation: str | None,
    registry: RelationWeightRegistry | None = None,
) -> float:
    return (registry or _DEFAULT_REGISTRY).traversal_cost(relation)


def edge_importance(
    edata: Mapping | None,
    registry: RelationWeightRegistry | None = None,
) -> float:
    return (registry or _DEFAULT_REGISTRY).edge_importance(edata)


def edge_cost(
    edata: Mapping | None,
    registry: RelationWeightRegistry | None = None,
) -> float:
    return (registry or _DEFAULT_REGISTRY).edge_cost(edata)
