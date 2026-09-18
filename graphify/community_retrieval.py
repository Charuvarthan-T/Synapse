"""Hierarchical community-aware retrieval on top of weighted graph retrieval.

Flow:
  query seeds → rank communities → restrict search space → weighted expand

Falls back to the full-graph weighted pipeline when community signal is weak
or detection/indexing fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import networkx as nx

from graphify.community_detection import CommunityDetector
from graphify.community_index import CommunityIndex
from graphify.weighted_retrieval import TraversalResult, expand_neighborhood
from graphify.weights import RelationWeightRegistry

DEFAULT_MIN_CONFIDENCE = 0.55
DEFAULT_MAX_COMMUNITIES = 2
_SEED_SCORE_WEIGHT = 10.0
_NAME_TERM_WEIGHT = 3.0
_LABEL_TERM_WEIGHT = 1.0
_REP_TERM_WEIGHT = 2.0


@dataclass(frozen=True)
class CommunitySelection:
    """Outcome of community ranking before node-level retrieval."""

    community_ids: tuple[int, ...]
    scores: dict[int, float]
    confidence: float
    used_fallback: bool
    reason: str

    @property
    def selected(self) -> bool:
        return not self.used_fallback and bool(self.community_ids)


def _normalize_terms(terms: list[str] | None) -> list[str]:
    if not terms:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        tok = re.sub(r"[^\w]+", "", str(term).lower())
        if len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _text_hits(text: str, terms: list[str]) -> int:
    if not text or not terms:
        return 0
    hay = text.lower()
    return sum(1 for term in terms if term in hay)


def score_communities(
    index: CommunityIndex,
    G: nx.Graph,
    *,
    seeds: list[str],
    query_terms: list[str] | None = None,
) -> dict[int, float]:
    """Score communities by seed membership and lexical overlap."""
    terms = _normalize_terms(query_terms)
    scores: dict[int, float] = {cid: 0.0 for cid in index.ids()}

    for seed in seeds:
        cid = index.community_of(seed)
        if cid is not None and cid in scores:
            scores[cid] += _SEED_SCORE_WEIGHT

    if not terms:
        return scores

    for cid, record in index.by_id.items():
        if record.name:
            scores[cid] += _NAME_TERM_WEIGHT * _text_hits(record.name, terms)
        for rep in record.representatives:
            data = G.nodes[rep] if rep in G else {}
            label = str(data.get("label") or rep)
            scores[cid] += _REP_TERM_WEIGHT * _text_hits(label, terms)
        # Sample member labels (representatives already covered; scan a few more).
        extra = [n for n in sorted(record.members, key=str) if n not in record.representatives][:8]
        for node in extra:
            data = G.nodes[node] if node in G else {}
            label = str(data.get("label") or node)
            scores[cid] += _LABEL_TERM_WEIGHT * _text_hits(label, terms)
    return scores


def select_communities(
    index: CommunityIndex,
    G: nx.Graph,
    *,
    seeds: list[str],
    query_terms: list[str] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_communities: int = DEFAULT_MAX_COMMUNITIES,
) -> CommunitySelection:
    """Pick the most relevant communities or signal a full-graph fallback."""
    if index.is_empty:
        return CommunitySelection(
            community_ids=(),
            scores={},
            confidence=0.0,
            used_fallback=True,
            reason="no_communities",
        )
    if not seeds:
        return CommunitySelection(
            community_ids=(),
            scores={},
            confidence=0.0,
            used_fallback=True,
            reason="no_seeds",
        )
    if index.community_count <= 1:
        only = tuple(index.ids())
        return CommunitySelection(
            community_ids=only,
            scores={only[0]: 1.0} if only else {},
            confidence=1.0,
            used_fallback=True,
            reason="single_community",
        )

    raw_scores = score_communities(index, G, seeds=seeds, query_terms=query_terms)
    ranked = sorted(raw_scores.items(), key=lambda item: (-item[1], item[0]))
    positive = [(cid, score) for cid, score in ranked if score > 0]
    if not positive:
        return CommunitySelection(
            community_ids=(),
            scores=raw_scores,
            confidence=0.0,
            used_fallback=True,
            reason="no_signal",
        )

    top_score = positive[0][1]
    second_score = positive[1][1] if len(positive) > 1 else 0.0
    gap_confidence = (
        top_score / (top_score + second_score) if (top_score + second_score) > 0 else 0.0
    )

    limit = max(1, int(max_communities))
    selected_ids = [cid for cid, _ in positive[:limit]]
    seeds_in = sum(1 for seed in seeds if index.community_of(seed) in set(selected_ids))
    if seeds_in == 0:
        return CommunitySelection(
            community_ids=(),
            scores=raw_scores,
            confidence=gap_confidence,
            used_fallback=True,
            reason="seeds_outside",
        )

    coverage = seeds_in / len(seeds)
    confidence = gap_confidence * (0.5 + 0.5 * coverage)
    if confidence < min_confidence:
        return CommunitySelection(
            community_ids=tuple(selected_ids),
            scores=raw_scores,
            confidence=confidence,
            used_fallback=True,
            reason="low_confidence",
        )

    return CommunitySelection(
        community_ids=tuple(selected_ids),
        scores=raw_scores,
        confidence=confidence,
        used_fallback=False,
        reason="selected",
    )


def restrict_to_communities(
    G: nx.Graph,
    index: CommunityIndex,
    community_ids: tuple[int, ...] | list[int],
    *,
    always_include: list[str] | None = None,
) -> nx.Graph:
    """Induce a subgraph over selected community members (plus required seeds)."""
    keep: set[str] = set(always_include or [])
    for cid in community_ids:
        keep |= set(index.members_of(int(cid)))
    keep &= set(G.nodes())
    if not keep:
        return G
    return G.subgraph(keep)


def prepare_community_scope(
    G: nx.Graph,
    seeds: list[str],
    *,
    query_terms: list[str] | None = None,
    index: CommunityIndex | None = None,
    detector: CommunityDetector | None = None,
    algorithm: str = "auto",
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_communities: int = DEFAULT_MAX_COMMUNITIES,
) -> tuple[nx.Graph, CommunitySelection, CommunityIndex]:
    """Resolve community index, select scope, and return the graph to traverse."""
    active_index = index or CommunityIndex.build(G, algorithm=algorithm, detector=detector)
    selection = select_communities(
        active_index,
        G,
        seeds=seeds,
        query_terms=query_terms,
        min_confidence=min_confidence,
        max_communities=max_communities,
    )
    if not selection.selected:
        return G, selection, active_index
    scoped = restrict_to_communities(
        G,
        active_index,
        selection.community_ids,
        always_include=seeds,
    )
    return scoped, selection, active_index


def community_aware_expand(
    G: nx.Graph,
    seeds: list[str],
    depth: int,
    *,
    mode: str = "bfs",
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
    query_terms: list[str] | None = None,
    index: CommunityIndex | None = None,
    detector: CommunityDetector | None = None,
    algorithm: str = "auto",
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_communities: int = DEFAULT_MAX_COMMUNITIES,
    community_aware: bool = True,
) -> tuple[TraversalResult, CommunitySelection, nx.Graph]:
    """Community-scoped expansion that delegates to weighted ``expand_neighborhood``.

    When ``community_aware`` is false or selection falls back, runs existing
    weighted/unweighted retrieval on the full graph.
    """
    if not community_aware:
        selection = CommunitySelection(
            community_ids=(),
            scores={},
            confidence=0.0,
            used_fallback=True,
            reason="disabled",
        )
        result = expand_neighborhood(
            G,
            seeds,
            depth,
            mode=mode,
            weighted=weighted,
            registry=registry,
        )
        return result, selection, G

    scoped, selection, _index = prepare_community_scope(
        G,
        seeds,
        query_terms=query_terms,
        index=index,
        detector=detector,
        algorithm=algorithm,
        min_confidence=min_confidence,
        max_communities=max_communities,
    )
    result = expand_neighborhood(
        scoped,
        seeds,
        depth,
        mode=mode,
        weighted=weighted,
        registry=registry,
    )
    return result, selection, scoped
