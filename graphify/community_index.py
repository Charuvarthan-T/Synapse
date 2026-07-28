"""Lightweight community metadata for hierarchical retrieval."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from graphify.community_detection import (
    CommunityDetector,
    detect_communities,
    get_community_detector,
    partition_to_communities,
)
from graphify.cluster import cohesion_score

DEFAULT_REPRESENTATIVE_COUNT = 3


@dataclass(frozen=True)
class CommunityRecord:
    """Metadata for a single community."""

    community_id: int
    members: frozenset[str]
    representatives: tuple[str, ...]
    size: int
    name: str | None = None
    cohesion: float | None = None


@dataclass(frozen=True)
class CommunityIndex:
    """Reusable community assignment and lookup structure."""

    by_id: dict[int, CommunityRecord]
    node_to_community: dict[str, int]

    @property
    def is_empty(self) -> bool:
        return not self.by_id

    @property
    def community_count(self) -> int:
        return len(self.by_id)

    def community_of(self, node_id: str) -> int | None:
        return self.node_to_community.get(node_id)

    def members_of(self, community_id: int) -> frozenset[str]:
        record = self.by_id.get(community_id)
        return record.members if record else frozenset()

    def record(self, community_id: int) -> CommunityRecord | None:
        return self.by_id.get(community_id)

    def ids(self) -> list[int]:
        return sorted(self.by_id)

    @classmethod
    def from_partition(
        cls,
        G: nx.Graph,
        partition: dict[str, int],
        *,
        representative_count: int = DEFAULT_REPRESENTATIVE_COUNT,
        compute_cohesion: bool = False,
    ) -> CommunityIndex:
        """Build an index from an explicit ``{node: community_id}`` map."""
        if not partition:
            return cls(by_id={}, node_to_community={})

        grouped = partition_to_communities(partition)
        by_id: dict[int, CommunityRecord] = {}
        node_to_community: dict[str, int] = {}

        for cid, members in grouped.items():
            member_set = frozenset(members)
            for node in member_set:
                node_to_community[node] = cid
            reps = _pick_representatives(G, members, representative_count)
            name = _community_name(G, members, reps)
            cohesion = cohesion_score(G, list(members)) if compute_cohesion else None
            by_id[cid] = CommunityRecord(
                community_id=cid,
                members=member_set,
                representatives=reps,
                size=len(member_set),
                name=name,
                cohesion=cohesion,
            )
        return cls(by_id=by_id, node_to_community=node_to_community)

    @classmethod
    def from_graph_attributes(
        cls,
        G: nx.Graph,
        *,
        representative_count: int = DEFAULT_REPRESENTATIVE_COUNT,
        compute_cohesion: bool = False,
    ) -> CommunityIndex | None:
        """Load communities from node ``community`` attributes when present."""
        partition: dict[str, int] = {}
        for node_id, data in G.nodes(data=True):
            cid = data.get("community")
            if cid is None:
                continue
            try:
                partition[str(node_id)] = int(cid)
            except (TypeError, ValueError):
                continue
        if not partition:
            return None
        return cls.from_partition(
            G,
            partition,
            representative_count=representative_count,
            compute_cohesion=compute_cohesion,
        )

    @classmethod
    def build(
        cls,
        G: nx.Graph,
        *,
        prefer_stored: bool = True,
        algorithm: str = "auto",
        detector: CommunityDetector | None = None,
        representative_count: int = DEFAULT_REPRESENTATIVE_COUNT,
        compute_cohesion: bool = False,
    ) -> CommunityIndex:
        """Prefer stored node attributes; otherwise run community detection."""
        if prefer_stored:
            stored = cls.from_graph_attributes(
                G,
                representative_count=representative_count,
                compute_cohesion=compute_cohesion,
            )
            if stored is not None and not stored.is_empty:
                return stored

        if G.number_of_nodes() == 0:
            return cls(by_id={}, node_to_community={})

        active = detector or get_community_detector(algorithm)
        try:
            partition = detect_communities(G, detector=active)
        except Exception:
            return cls(by_id={}, node_to_community={})
        return cls.from_partition(
            G,
            partition,
            representative_count=representative_count,
            compute_cohesion=compute_cohesion,
        )


def _pick_representatives(
    G: nx.Graph,
    members: list[str],
    count: int,
) -> tuple[str, ...]:
    present = [n for n in members if n in G]
    if not present:
        return tuple(sorted(members, key=str)[: max(0, count)])
    ranked = sorted(present, key=lambda n: (-int(G.degree(n)), str(n)))
    return tuple(ranked[: max(0, count)])


def _community_name(
    G: nx.Graph, members: list[str], representatives: tuple[str, ...]
) -> str | None:
    for node in representatives:
        data = G.nodes[node] if node in G else {}
        stored = data.get("community_name")
        if stored:
            return str(stored)
    if representatives:
        hub = representatives[0]
        data = G.nodes[hub] if hub in G else {}
        label = data.get("label") or hub
        name = str(label).strip()
        if name.endswith("()"):
            name = name[:-2]
        return name or None
    if members:
        return str(members[0])
    return None
