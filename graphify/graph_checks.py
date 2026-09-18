"""Shared graph lookups for claim validation and pre-execution checks.

Answers structural questions against an existing NetworkX knowledge graph
without inventing facts. Absence of evidence yields ``UNKNOWN``, never a
fabricated ``SUPPORTED`` / ``VALID`` result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

import networkx as nx

from graphify.build import edge_datas

# Structural + semantic relation names commonly asserted by agents / LLMs.
CALL_RELATIONS = frozenset({"calls", "indirect_call"})
IMPORT_RELATIONS = frozenset({"imports", "imports_from", "uses"})
INHERIT_RELATIONS = frozenset({"inherits", "extends", "implements", "overrides", "mixes_in"})
METHOD_RELATIONS = frozenset({"method", "contains", "defines"})


class Verdict(str, Enum):
    """Shared verdict vocabulary.

    C2 surfaces these as SUPPORTED / CONTRADICTED / UNKNOWN.
    C3 maps SUPPORTED→VALID, CONTRADICTED→INVALID, UNKNOWN→UNKNOWN.
    """

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"

    @property
    def as_action_status(self) -> str:
        if self is Verdict.SUPPORTED:
            return "VALID"
        if self is Verdict.CONTRADICTED:
            return "INVALID"
        return "UNKNOWN"


@dataclass(frozen=True)
class CheckEvidence:
    """One piece of graph evidence for a check outcome."""

    kind: str
    detail: str
    node_ids: tuple[str, ...] = ()
    relation: str | None = None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single graph-verifiable assertion."""

    verdict: Verdict
    claim: str
    reason: str
    evidence: tuple[CheckEvidence, ...] = ()

    @property
    def status(self) -> str:
        """C3-facing status label (VALID / INVALID / UNKNOWN)."""
        return self.verdict.as_action_status

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "status": self.status,
            "claim": self.claim,
            "reason": self.reason,
            "evidence": [
                {
                    "kind": e.kind,
                    "detail": e.detail,
                    "node_ids": list(e.node_ids),
                    "relation": e.relation,
                }
                for e in self.evidence
            ],
        }


def _norm(text: str) -> str:
    return " ".join(str(text).lower().replace("\\", "/").replace("::", ".").split())


def _bare(label: str) -> str:
    s = str(label).strip()
    if s.endswith("()"):
        s = s[:-2]
    return s.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def resolve_nodes(G: nx.Graph, query: str, *, limit: int = 8) -> list[str]:
    """Resolve a symbol/path query to node IDs (best matches first).

    Matching precedence: exact id → exact label → bare-name exact →
    suffix/prefix → substring. Empty query → empty list.
    """
    raw = str(query or "").strip()
    if not raw or G.number_of_nodes() == 0:
        return []
    nq = _norm(raw)
    bare_q = _norm(_bare(raw))

    exact_id: list[str] = []
    exact_label: list[str] = []
    bare_exact: list[str] = []
    suffix: list[str] = []
    substr: list[str] = []

    for nid, data in G.nodes(data=True):
        label = str(data.get("label") or nid)
        nid_l = str(nid).lower()
        lab_n = _norm(label)
        bare_l = _norm(_bare(label))
        source = _norm(str(data.get("source_file") or ""))

        if nq == nid_l or nq == _norm(nid):
            exact_id.append(str(nid))
        elif nq == lab_n or nq == lab_n.rstrip("()"):
            exact_label.append(str(nid))
        elif bare_q and bare_q == bare_l:
            bare_exact.append(str(nid))
        elif source and (nq == source or source.endswith("/" + nq) or source.endswith(nq)):
            suffix.append(str(nid))
        elif nq and (nq in lab_n or nq in nid_l or (bare_q and bare_q in bare_l)):
            substr.append(str(nid))

    ordered: list[str] = []
    seen: set[str] = set()
    for group in (exact_id, exact_label, bare_exact, suffix, substr):
        for nid in group:
            if nid not in seen:
                seen.add(nid)
                ordered.append(nid)
            if len(ordered) >= limit:
                return ordered
    return ordered


def _node_label(G: nx.Graph, nid: str) -> str:
    data = G.nodes.get(nid) or {}
    return str(data.get("label") or nid)


def _iter_edge_relations(edata: dict) -> Iterable[str]:
    rel = edata.get("relation")
    if rel:
        yield str(rel)
    secondary = edata.get("secondary_relations")
    if isinstance(secondary, list):
        for item in secondary:
            if isinstance(item, dict) and item.get("relation"):
                yield str(item["relation"])


def _edges_between(G: nx.Graph, u: str, v: str) -> list[dict]:
    if not G.has_node(u) or not G.has_node(v):
        return []
    found: list[dict] = []
    if G.has_edge(u, v):
        found.extend(edge_datas(G, u, v))
    if not G.is_directed() and G.has_edge(v, u):
        found.extend(edge_datas(G, v, u))
    elif G.is_directed() and G.has_edge(v, u):
        # Still collect reverse for undirected-style inspection of "related".
        pass
    return found


def _has_relation(G: nx.Graph, u: str, v: str, allowed: frozenset[str]) -> tuple[bool, str | None]:
    for edata in _edges_between(G, u, v):
        for rel in _iter_edge_relations(edata):
            if rel in allowed:
                return True, rel
    # Directed: also accept u→v only for directed graphs (already covered).
    if G.is_directed():
        return False, None
    return False, None


def _neighbors_with_relations(
    G: nx.Graph, nid: str, allowed: frozenset[str]
) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if not G.has_node(nid):
        return hits
    for _, nbr, edata in G.edges(nid, data=True):
        for rel in _iter_edge_relations(edata):
            if rel in allowed:
                hits.append((str(nbr), rel))
    if G.is_directed():
        for pred, _, edata in G.in_edges(nid, data=True):
            for rel in _iter_edge_relations(edata):
                if rel in allowed:
                    hits.append((str(pred), rel))
    return hits


def check_exists(G: nx.Graph, symbol: str) -> CheckResult:
    """Does this symbol/module/file appear in the graph?"""
    claim = f"exists:{symbol}"
    matches = resolve_nodes(G, symbol)
    if matches:
        top = matches[0]
        return CheckResult(
            verdict=Verdict.SUPPORTED,
            claim=claim,
            reason=f"Symbol resolved to graph node {_node_label(G, top)!r}",
            evidence=(
                CheckEvidence(
                    kind="node",
                    detail=f"matched {_node_label(G, top)}",
                    node_ids=(top,),
                ),
            ),
        )
    return CheckResult(
        verdict=Verdict.UNKNOWN,
        claim=claim,
        reason="No matching node in the repository graph (insufficient evidence to reject)",
        evidence=(),
    )


def check_relation(
    G: nx.Graph,
    source: str,
    relation: str,
    target: str,
    *,
    allowed: frozenset[str] | None = None,
) -> CheckResult:
    """Validate that ``source -[relation]→ target`` is supported by the graph."""
    rel = str(relation).strip().lower()
    claim = f"{source} -[{rel}]→ {target}"
    allowed_set = allowed or frozenset({rel})

    src_ids = resolve_nodes(G, source)
    tgt_ids = resolve_nodes(G, target)

    if not src_ids and not tgt_ids:
        return CheckResult(
            verdict=Verdict.UNKNOWN,
            claim=claim,
            reason="Neither endpoint resolved in the graph",
        )
    if not src_ids:
        return CheckResult(
            verdict=Verdict.UNKNOWN,
            claim=claim,
            reason=f"Source {source!r} not found in the graph",
            evidence=(
                CheckEvidence(
                    kind="missing_node",
                    detail=f"unresolved source {source!r}",
                    node_ids=tuple(tgt_ids[:3]),
                ),
            ),
        )
    if not tgt_ids:
        # Source known, target unknown — not enough to contradict.
        return CheckResult(
            verdict=Verdict.UNKNOWN,
            claim=claim,
            reason=f"Target {target!r} not found in the graph",
            evidence=(
                CheckEvidence(
                    kind="node",
                    detail=f"source matched {_node_label(G, src_ids[0])}",
                    node_ids=(src_ids[0],),
                ),
            ),
        )

    # Try endpoint pairs in match-rank order.
    for u in src_ids[:4]:
        for v in tgt_ids[:4]:
            ok, found_rel = _has_relation(G, u, v, allowed_set)
            if ok:
                return CheckResult(
                    verdict=Verdict.SUPPORTED,
                    claim=claim,
                    reason=f"Found relation {found_rel!r} between endpoints",
                    evidence=(
                        CheckEvidence(
                            kind="edge",
                            detail=f"{_node_label(G, u)} -[{found_rel}]→ {_node_label(G, v)}",
                            node_ids=(u, v),
                            relation=found_rel,
                        ),
                    ),
                )

    # Both endpoints exist. Contradiction only when the graph shows a clear
    # exclusive alternate for inheritance-like claims; otherwise UNKNOWN.
    if rel in INHERIT_RELATIONS or allowed_set & INHERIT_RELATIONS:
        u = src_ids[0]
        parents = [
            (nbr, r)
            for nbr, r in _neighbors_with_relations(G, u, INHERIT_RELATIONS)
            if r in (allowed_set | INHERIT_RELATIONS)
        ]
        # Prefer outbound inherits/extends from u.
        outbound: list[tuple[str, str]] = []
        if G.has_node(u):
            for _, nbr, edata in G.edges(u, data=True):
                for r in _iter_edge_relations(edata):
                    if r in INHERIT_RELATIONS:
                        outbound.append((str(nbr), r))
        tgt_set = set(tgt_ids)
        if outbound and all(nbr not in tgt_set for nbr, _ in outbound):
            alt = outbound[0]
            return CheckResult(
                verdict=Verdict.CONTRADICTED,
                claim=claim,
                reason=(
                    f"{_node_label(G, u)} inherits/extends "
                    f"{_node_label(G, alt[0])} rather than {target!r}"
                ),
                evidence=(
                    CheckEvidence(
                        kind="edge",
                        detail=f"{_node_label(G, u)} -[{alt[1]}]→ {_node_label(G, alt[0])}",
                        node_ids=(u, alt[0]),
                        relation=alt[1],
                    ),
                ),
            )

    return CheckResult(
        verdict=Verdict.UNKNOWN,
        claim=claim,
        reason=(
            "Both endpoints exist but no matching edge was found "
            "(graph may be incomplete — not treated as false)"
        ),
        evidence=(
            CheckEvidence(
                kind="node",
                detail=f"source={_node_label(G, src_ids[0])}",
                node_ids=(src_ids[0],),
            ),
            CheckEvidence(
                kind="node",
                detail=f"target={_node_label(G, tgt_ids[0])}",
                node_ids=(tgt_ids[0],),
            ),
        ),
    )


def check_calls(G: nx.Graph, caller: str, callee: str) -> CheckResult:
    return check_relation(G, caller, "calls", callee, allowed=CALL_RELATIONS)


def check_imports(G: nx.Graph, importer: str, imported: str) -> CheckResult:
    return check_relation(G, importer, "imports", imported, allowed=IMPORT_RELATIONS)


def check_inherits(G: nx.Graph, child: str, parent: str) -> CheckResult:
    return check_relation(G, child, "inherits", parent, allowed=INHERIT_RELATIONS)


def check_method_of(G: nx.Graph, owner: str, method: str) -> CheckResult:
    """Validate that ``method`` is a known member of ``owner``."""
    claim = f"method:{owner}.{method}"
    owners = resolve_nodes(G, owner)
    methods = resolve_nodes(G, method)
    # Also try qualified name.
    qualified = resolve_nodes(G, f"{owner}.{method}")
    if qualified:
        return CheckResult(
            verdict=Verdict.SUPPORTED,
            claim=claim,
            reason=f"Qualified symbol resolved to {_node_label(G, qualified[0])!r}",
            evidence=(
                CheckEvidence(
                    kind="node",
                    detail=f"matched {_node_label(G, qualified[0])}",
                    node_ids=(qualified[0],),
                ),
            ),
        )
    if not owners:
        return CheckResult(
            verdict=Verdict.UNKNOWN,
            claim=claim,
            reason=f"Owner {owner!r} not found in the graph",
        )
    owner_id = owners[0]
    # Method nodes that are neighbors via method/contains.
    member_hits = _neighbors_with_relations(G, owner_id, METHOD_RELATIONS)
    method_bare = _norm(_bare(method))
    for nbr, rel in member_hits:
        if _norm(_bare(_node_label(G, nbr))) == method_bare or _norm(nbr).endswith(
            "." + method_bare
        ):
            return CheckResult(
                verdict=Verdict.SUPPORTED,
                claim=claim,
                reason=f"Found {rel!r} edge to member {_node_label(G, nbr)!r}",
                evidence=(
                    CheckEvidence(
                        kind="edge",
                        detail=f"{_node_label(G, owner_id)} -[{rel}]→ {_node_label(G, nbr)}",
                        node_ids=(owner_id, nbr),
                        relation=rel,
                    ),
                ),
            )
    # Direct method node match whose label is bare method name under same file?
    if methods:
        for mid in methods[:5]:
            if G.has_edge(owner_id, mid) or G.has_edge(mid, owner_id):
                return CheckResult(
                    verdict=Verdict.SUPPORTED,
                    claim=claim,
                    reason="Owner and method are directly connected",
                    evidence=(
                        CheckEvidence(
                            kind="edge",
                            detail=f"{_node_label(G, owner_id)} ↔ {_node_label(G, mid)}",
                            node_ids=(owner_id, mid),
                        ),
                    ),
                )
        # Owner known, similarly named method exists elsewhere → still UNKNOWN
        # (could be a different class's method).
        return CheckResult(
            verdict=Verdict.UNKNOWN,
            claim=claim,
            reason=(
                f"Owner {_node_label(G, owner_id)!r} exists and a symbol resembling "
                f"{method!r} exists, but no membership edge was found"
            ),
            evidence=(
                CheckEvidence(
                    kind="node",
                    detail=f"owner={_node_label(G, owner_id)}",
                    node_ids=(owner_id,),
                ),
                CheckEvidence(
                    kind="node",
                    detail=f"candidate method={_node_label(G, methods[0])}",
                    node_ids=(methods[0],),
                ),
            ),
        )

    # Owner exists with method/contains edges but none match → CONTRADICTED
    # only when the owner has an explicit member inventory (at least one method edge).
    if member_hits:
        return CheckResult(
            verdict=Verdict.CONTRADICTED,
            claim=claim,
            reason=(
                f"{_node_label(G, owner_id)!r} exists and has known members, "
                f"but {method!r} is not among them"
            ),
            evidence=(
                CheckEvidence(
                    kind="node",
                    detail=f"owner={_node_label(G, owner_id)}",
                    node_ids=(owner_id,),
                ),
                CheckEvidence(
                    kind="members",
                    detail="known members: "
                    + ", ".join(_node_label(G, n) for n, _ in member_hits[:8]),
                    node_ids=tuple(n for n, _ in member_hits[:8]),
                ),
            ),
        )

    return CheckResult(
        verdict=Verdict.UNKNOWN,
        claim=claim,
        reason=(
            f"Owner {_node_label(G, owner_id)!r} exists but has no method inventory "
            "in the graph — cannot confirm or deny the member"
        ),
        evidence=(
            CheckEvidence(
                kind="node",
                detail=f"owner={_node_label(G, owner_id)}",
                node_ids=(owner_id,),
            ),
        ),
    )


@dataclass
class AggregateReport:
    """Roll-up of multiple :class:`CheckResult` values."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(r.verdict is Verdict.CONTRADICTED for r in self.results):
            return Verdict.CONTRADICTED
        if self.results and all(r.verdict is Verdict.SUPPORTED for r in self.results):
            return Verdict.SUPPORTED
        if any(r.verdict is Verdict.SUPPORTED for r in self.results) and not any(
            r.verdict is Verdict.CONTRADICTED for r in self.results
        ):
            # Mix of supported + unknown → still unknown overall for safety.
            if any(r.verdict is Verdict.UNKNOWN for r in self.results):
                return Verdict.UNKNOWN
            return Verdict.SUPPORTED
        return Verdict.UNKNOWN

    @property
    def status(self) -> str:
        return self.verdict.as_action_status

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "status": self.status,
            "results": [r.to_dict() for r in self.results],
            "counts": {
                "supported": sum(1 for r in self.results if r.verdict is Verdict.SUPPORTED),
                "contradicted": sum(
                    1 for r in self.results if r.verdict is Verdict.CONTRADICTED
                ),
                "unknown": sum(1 for r in self.results if r.verdict is Verdict.UNKNOWN),
            },
        }
