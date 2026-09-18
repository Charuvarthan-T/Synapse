"""Structured, graph-verifiable claims extracted from LLM reasoning.

Claims are intentionally small and typed so :mod:`graphify.graph_checks`
can validate them without free-form NLP over the final answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

import networkx as nx

from graphify.graph_checks import (
    CALL_RELATIONS,
    IMPORT_RELATIONS,
    INHERIT_RELATIONS,
    AggregateReport,
    CheckResult,
    Verdict,
    check_calls,
    check_exists,
    check_imports,
    check_inherits,
    check_method_of,
    check_relation,
)
from graphify.semantic_relations import SEMANTIC_RELATION_TYPES

CLAIM_TYPES = frozenset(
    {
        "exists",
        "calls",
        "imports",
        "inherits",
        "method_of",
        "relation",
    }
)


@dataclass(frozen=True)
class Claim:
    """One graph-checkable assertion produced by an LLM (or a test harness)."""

    type: str
    source: str | None = None
    target: str | None = None
    relation: str | None = None
    symbol: str | None = None
    raw: dict[str, Any] | None = None

    def describe(self) -> str:
        if self.type == "exists":
            return f"exists:{self.symbol or self.source}"
        if self.type == "method_of":
            return f"method:{self.source}.{self.target}"
        rel = self.relation or self.type
        return f"{self.source} -[{rel}]→ {self.target}"


def _coerce_claim(obj: Any) -> Claim | None:
    if not isinstance(obj, dict):
        return None
    ctype = str(obj.get("type") or obj.get("claim_type") or "").strip().lower()
    if ctype not in CLAIM_TYPES:
        # Allow relation name as type shorthand: {"type":"calls","source":..,"target":..}
        if ctype in CALL_RELATIONS:
            return Claim(type="calls", source=str(obj.get("source") or ""), target=str(obj.get("target") or ""), relation=ctype, raw=obj)
        if ctype in IMPORT_RELATIONS:
            return Claim(type="imports", source=str(obj.get("source") or ""), target=str(obj.get("target") or ""), relation=ctype, raw=obj)
        if ctype in INHERIT_RELATIONS:
            return Claim(type="inherits", source=str(obj.get("source") or ""), target=str(obj.get("target") or ""), relation=ctype, raw=obj)
        if ctype in SEMANTIC_RELATION_TYPES or ctype:
            src = obj.get("source")
            tgt = obj.get("target")
            if src and tgt:
                return Claim(
                    type="relation",
                    source=str(src),
                    target=str(tgt),
                    relation=ctype if ctype else str(obj.get("relation") or "related_to"),
                    raw=obj,
                )
        return None

    if ctype == "exists":
        symbol = obj.get("symbol") or obj.get("name") or obj.get("source")
        if not symbol:
            return None
        return Claim(type="exists", symbol=str(symbol), source=str(symbol), raw=obj)

    if ctype == "method_of":
        owner = obj.get("source") or obj.get("owner") or obj.get("class")
        method = obj.get("target") or obj.get("method") or obj.get("name")
        if not owner or not method:
            return None
        return Claim(type="method_of", source=str(owner), target=str(method), raw=obj)

    source = obj.get("source") or obj.get("caller") or obj.get("from")
    target = obj.get("target") or obj.get("callee") or obj.get("to")
    if not source or not target:
        return None
    relation = obj.get("relation")
    return Claim(
        type=ctype,
        source=str(source),
        target=str(target),
        relation=str(relation) if relation else None,
        raw=obj,
    )


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_blob(text: str) -> Any | None:
    """Best-effort JSON object/array extraction from free-form model text."""
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    for match in _JSON_FENCE.finditer(raw):
        candidates.insert(0, match.group(1))
    brace = raw.find("{")
    if brace >= 0:
        candidates.append(raw[brace:])
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def parse_claims(text: str) -> tuple[list[Claim], list[str]]:
    """Parse claims from LLM output (JSON object/array, optionally fenced).

    Returns ``(claims, errors)``. Never raises.
    """
    errors: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return [], ["empty claim payload"]

    data = extract_json_blob(raw)
    if data is None:
        return [], ["could not parse JSON claims from model output"]

    items: list[Any]
    if isinstance(data, dict):
        if "claims" in data and isinstance(data["claims"], list):
            items = data["claims"]
        else:
            items = [data]
    elif isinstance(data, list):
        items = data
    else:
        return [], ["claims JSON must be an object or array"]

    claims: list[Claim] = []
    for i, item in enumerate(items):
        claim = _coerce_claim(item)
        if claim is None:
            errors.append(f"claims[{i}] is not a recognised claim object")
            continue
        claims.append(claim)
    return claims, errors


def validate_claim(G: nx.Graph, claim: Claim) -> CheckResult:
    """Dispatch one claim to the shared graph checkers."""
    if claim.type == "exists":
        return check_exists(G, claim.symbol or claim.source or "")
    if claim.type == "calls":
        return check_calls(G, claim.source or "", claim.target or "")
    if claim.type == "imports":
        return check_imports(G, claim.source or "", claim.target or "")
    if claim.type == "inherits":
        return check_inherits(G, claim.source or "", claim.target or "")
    if claim.type == "method_of":
        return check_method_of(G, claim.source or "", claim.target or "")
    if claim.type == "relation":
        rel = (claim.relation or "related_to").lower()
        allowed = frozenset({rel})
        if rel in CALL_RELATIONS:
            allowed = CALL_RELATIONS
        elif rel in IMPORT_RELATIONS:
            allowed = IMPORT_RELATIONS
        elif rel in INHERIT_RELATIONS:
            allowed = INHERIT_RELATIONS
        elif rel in SEMANTIC_RELATION_TYPES:
            allowed = frozenset(SEMANTIC_RELATION_TYPES) | frozenset({rel})
        return check_relation(G, claim.source or "", rel, claim.target or "", allowed=allowed)
    return CheckResult(
        verdict=Verdict.UNKNOWN,
        claim=claim.describe(),
        reason=f"Unsupported claim type {claim.type!r}",
    )


def validate_claims(G: nx.Graph, claims: Iterable[Claim]) -> AggregateReport:
    report = AggregateReport()
    for claim in claims:
        report.results.append(validate_claim(G, claim))
    return report
