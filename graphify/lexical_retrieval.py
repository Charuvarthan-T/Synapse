"""Lexical (BM25) retrieval over symbol source code, fused with graph ranking.

Graph seeding matches query terms against symbol *names*; this module also
matches them against what each symbol's *code* contains, the way BM25
retrieval over code chunks does. The two rankings are combined with
reciprocal rank fusion (RRF), so a symbol ranks high when either the graph
or the code text says it is relevant, and highest when both do.

Symbol ranges come from :mod:`graphify.code_context` (exact for Python,
next-symbol boundaries elsewhere). Files are read only from inside ``root``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import networkx as nx

from graphify.code_context import _FileCache, _end_line, _is_file_node, _start_line

DEFAULT_MAX_LINES = 80
HYBRID_LEXICAL_LIMIT = 15  # top BM25 symbols fused into a hybrid query's result
RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def code_tokens(text: str) -> list[str]:
    """Lower-cased identifiers plus their snake_case / camelCase parts."""
    tokens: list[str] = []
    for ident in _IDENT_RE.findall(text):
        low = ident.lower()
        if len(low) < 2:
            continue
        tokens.append(low)
        parts = [p.lower() for chunk in ident.split("_") for p in _CAMEL_RE.findall(chunk)]
        if len(parts) > 1:
            tokens.extend(p for p in parts if len(p) > 1 and p != low)
    return tokens


def symbol_documents(G: nx.Graph, root: str | Path, *, max_lines: int = DEFAULT_MAX_LINES) -> dict[str, list[str]]:
    """Token list of each code symbol's source range, keyed by node id."""
    cache = _FileCache(Path(root))
    docs: dict[str, list[str]] = {}
    for nid, d in G.nodes(data=True):
        source_file = str(d.get("source_file") or "")
        if not source_file or d.get("file_type", "code") != "code" or _is_file_node(d):
            continue
        start = _start_line(d.get("source_location"))
        lines = cache.lines(source_file) if start else None
        if not lines or start is None or start > len(lines):
            continue
        end = _end_line(cache, G, source_file, start, len(lines), max_lines)
        tokens = code_tokens("\n".join(lines[start - 1 : end]))
        if tokens:
            docs[str(nid)] = tokens
    return docs


def bm25_scores(docs: dict[str, list[str]], query: list[str]) -> dict[str, float]:
    """Okapi BM25 score of every document with at least one query term."""
    if not docs or not query:
        return {}
    n = len(docs)
    avgdl = sum(len(t) for t in docs.values()) / n
    q = set(query)
    df = Counter(term for tokens in docs.values() for term in q.intersection(tokens))
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
    scores: dict[str, float] = {}
    for nid, tokens in docs.items():
        tf = Counter(t for t in tokens if t in idf)
        if not tf:
            continue
        norm = BM25_K1 * (1 - BM25_B + BM25_B * len(tokens) / avgdl)
        scores[nid] = sum(idf[t] * f * (BM25_K1 + 1) / (f + norm) for t, f in tf.items())
    return scores


def lexical_ranking(G: nx.Graph, question: str, root: str | Path, *, max_lines: int = DEFAULT_MAX_LINES) -> list[str]:
    """Node ids ranked by BM25 of the question against their source code."""
    scores = bm25_scores(symbol_documents(G, root, max_lines=max_lines), code_tokens(question))
    return sorted(scores, key=lambda nid: (-scores[nid], nid))


def rrf_fuse(*rankings: list[str], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion; ties keep the first ranking's order."""
    score: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, nid in enumerate(ranking):
            score[nid] = score.get(nid, 0.0) + 1.0 / (k + rank + 1)
            first_seen.setdefault(nid, len(first_seen))
    return sorted(score, key=lambda nid: (-score[nid], first_seen[nid]))
