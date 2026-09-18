"""Pluggable community detection for hierarchical retrieval.

Algorithms are replaceable via :func:`get_community_detector`. Detection here is
independent of build-time clustering in :mod:`graphify.cluster`, but the ``auto``
detector reuses the same Leiden→Louvain preference for consistency.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Literal

import networkx as nx

AlgorithmName = Literal["auto", "leiden", "louvain", "label_propagation"]

_MIN_LOUVAIN_NODES = 3


class CommunityDetector(ABC):
    """Strategy interface for assigning nodes to communities."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable algorithm identifier."""

    @abstractmethod
    def detect(self, G: nx.Graph) -> dict[str, int]:
        """Return ``{node_id: community_id}``.

        Empty graphs return ``{}``. Small or edgeless graphs receive a
        deterministic one-community-per-component (or per-node) assignment.
        """


def _undirected_copy(G: nx.Graph) -> nx.Graph:
    if G.is_directed():
        return G.to_undirected()
    return G


def _singleton_partition(nodes: list[str]) -> dict[str, int]:
    return {node: idx for idx, node in enumerate(nodes)}


def _component_partition(G: nx.Graph) -> dict[str, int]:
    """Assign each connected component its own community id."""
    partition: dict[str, int] = {}
    for cid, component in enumerate(
        sorted(nx.connected_components(G), key=lambda c: (-len(c), sorted(map(str, c))))
    ):
        for node in component:
            partition[node] = cid
    return partition


def _prepare_graph(G: nx.Graph) -> tuple[nx.Graph, dict[str, int] | None]:
    """Normalize graph; return early partition for trivial cases."""
    if G.number_of_nodes() == 0:
        return G, {}
    und = _undirected_copy(G)
    if und.number_of_edges() == 0:
        return und, _singleton_partition(sorted(und.nodes(), key=str))
    if und.number_of_nodes() < _MIN_LOUVAIN_NODES:
        return und, _component_partition(und)
    return und, None


class LouvainCommunityDetector(CommunityDetector):
    """NetworkX Louvain (no extra dependencies)."""

    @property
    def name(self) -> str:
        return "louvain"

    def detect(self, G: nx.Graph) -> dict[str, int]:
        und, early = _prepare_graph(G)
        if early is not None:
            return early
        kwargs: dict = {"seed": 42, "threshold": 1e-4, "resolution": 1.0}
        if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
            kwargs["max_level"] = 10
        communities = nx.community.louvain_communities(und, **kwargs)
        return {node: cid for cid, nodes in enumerate(communities) for node in nodes}


class LeidenCommunityDetector(CommunityDetector):
    """graspologic Leiden when installed; otherwise Louvain."""

    def __init__(self) -> None:
        self._fallback = LouvainCommunityDetector()

    @property
    def name(self) -> str:
        return "leiden"

    def detect(self, G: nx.Graph) -> dict[str, int]:
        und, early = _prepare_graph(G)
        if early is not None:
            return early
        try:
            from graspologic.partition import leiden
        except ImportError:
            return self._fallback.detect(und)

        import contextlib
        import io
        import json
        import sys

        stable = nx.Graph()
        stable.add_nodes_from(sorted(und.nodes(), key=str))
        edge_rows = sorted(
            und.edges(data=True),
            key=lambda row: (
                str(row[0]),
                str(row[1]),
                json.dumps(row[2], sort_keys=True, ensure_ascii=False, default=str),
            ),
        )
        for src, tgt, attrs in edge_rows:
            stable.add_edge(src, tgt, **attrs)

        lsig = inspect.signature(leiden).parameters
        kwargs: dict = {}
        if "random_seed" in lsig:
            kwargs["random_seed"] = 42
        if "trials" in lsig:
            kwargs["trials"] = 1
        if "resolution" in lsig:
            kwargs["resolution"] = 1.0
        old_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()):
                result = leiden(stable, **kwargs)
        finally:
            sys.stderr = old_stderr
        return {node: int(cid) for node, cid in result.items()}


class LabelPropagationCommunityDetector(CommunityDetector):
    """NetworkX asynchronous label propagation."""

    @property
    def name(self) -> str:
        return "label_propagation"

    def detect(self, G: nx.Graph) -> dict[str, int]:
        und, early = _prepare_graph(G)
        if early is not None:
            return early
        communities = list(nx.community.asyn_lpa_communities(und, seed=42))
        communities.sort(key=lambda nodes: (-len(nodes), tuple(sorted(map(str, nodes)))))
        return {node: cid for cid, nodes in enumerate(communities) for node in nodes}


class AutoCommunityDetector(CommunityDetector):
    """Prefer Leiden, fall back to Louvain — matches build-time clustering preference."""

    def __init__(self) -> None:
        self._leiden = LeidenCommunityDetector()
        self._louvain = LouvainCommunityDetector()

    @property
    def name(self) -> str:
        return "auto"

    def detect(self, G: nx.Graph) -> dict[str, int]:
        try:
            from graspologic.partition import leiden  # noqa: F401
        except ImportError:
            return self._louvain.detect(G)
        return self._leiden.detect(G)


_DETECTORS: dict[str, type[CommunityDetector]] = {
    "auto": AutoCommunityDetector,
    "leiden": LeidenCommunityDetector,
    "louvain": LouvainCommunityDetector,
    "label_propagation": LabelPropagationCommunityDetector,
}


def get_community_detector(algorithm: AlgorithmName | str = "auto") -> CommunityDetector:
    """Factory for replaceable community detection algorithms."""
    key = (algorithm or "auto").strip().lower()
    try:
        cls = _DETECTORS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_DETECTORS))
        raise ValueError(
            f"unknown community algorithm {algorithm!r}; choose one of: {supported}"
        ) from exc
    return cls()


def detect_communities(
    G: nx.Graph,
    *,
    algorithm: AlgorithmName | str = "auto",
    detector: CommunityDetector | None = None,
) -> dict[str, int]:
    """Run community detection and return ``{node_id: community_id}``."""
    active = detector or get_community_detector(algorithm)
    return active.detect(G)


def partition_to_communities(partition: dict[str, int]) -> dict[int, list[str]]:
    """Convert ``{node: cid}`` into ``{cid: [nodes]}`` with sorted members."""
    communities: dict[int, list[str]] = {}
    for node, cid in partition.items():
        communities.setdefault(int(cid), []).append(node)
    return {cid: sorted(members, key=str) for cid, members in sorted(communities.items())}
