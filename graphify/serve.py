from __future__ import annotations
import json
import math
import re
import sys
from array import array
from pathlib import Path
from typing import NamedTuple
import networkx as nx
from networkx.readwrite import json_graph
from graphify.security import sanitize_label, check_graph_file_size_cap
from graphify.build import edge_data, edge_datas
from graphify.paths import default_graph_json as _default_graph_json
from graphify.weights import RelationWeightRegistry
from graphify.community_retrieval import (
    CommunitySelection,
    community_aware_expand,
)
from graphify.weighted_retrieval import (
    expand_neighborhood,
    find_shortest_path,
    rank_edges_for_retrieval,
    rank_nodes_for_retrieval,
)

try:
    import jieba as _jieba  # type: ignore[import-untyped]
except ImportError:
    _jieba = None


def _load_graph(graph_path: str) -> nx.Graph:
    try:
        resolved = Path(graph_path).resolve()
        if resolved.suffix != ".json":
            raise ValueError(f"Graph path must be a .json file, got: {graph_path!r}")
        if not resolved.exists():
            raise FileNotFoundError(f"Graph file not found: {resolved}")
        check_graph_file_size_cap(resolved)
        safe = resolved
        data = json.loads(safe.read_text(encoding="utf-8"))
        if "links" not in data and "edges" in data:
            data = dict(data, links=data["edges"])
        data = {**data, "directed": True}
        try:
            from graphify.build import graph_has_legacy_ids as _legacy

            if _legacy(data.get("nodes", [])):
                print(
                    "[graphify] note: this graph uses the pre-#1504 node-ID scheme; "
                    "rebuild with `graphify extract --force` for path-qualified IDs.",
                    file=sys.stderr,
                )
        except Exception:
            pass
        try:
            G = json_graph.node_link_graph(data, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(data)
        try:
            from graphify.reflect import load_learning_overlay as _llo

            G.graph["_learning_overlay"] = _llo(resolved)
        except Exception:
            G.graph["_learning_overlay"] = {}
        return G
    except json.JSONDecodeError as exc:
        print(
            f"error: graph.json is corrupted ({exc}). Re-run /graphify to rebuild.", file=sys.stderr
        )
        sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _communities_from_graph(G: nx.Graph) -> dict[int, list[str]]:
    """Reconstruct community dict from community property stored on nodes."""
    communities: dict[int, list[str]] = {}
    for node_id, data in G.nodes(data=True):
        cid = data.get("community")
        if cid is not None:
            communities.setdefault(int(cid), []).append(node_id)
    return communities


def _strip_diacritics(text: str | None) -> str:
    import unicodedata

    if not isinstance(text, str):
        text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _search_tokens(text: str) -> list[str]:
    """Split text into word tokens, stripping punctuation and diacritics."""
    return re.findall(r"\w+", _strip_diacritics(str(text)).lower())


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _segment_chinese(text: str) -> list[str]:
    """Segment Chinese text and keep the original term for exact matching."""
    if _jieba is not None:
        segments = [w for w in _jieba.cut(text) if len(w.strip()) > 0]
    else:
        segments = [text[i : i + 2] for i in range(len(text) - 1)] or [text]
    if len(text) > 1 and text not in segments:
        segments.append(text)
    return segments


def _is_searchable(term: str) -> bool:
    """True if term is Chinese, non-English, or an English word longer than 2 chars."""
    if all("a" <= ch <= "z" for ch in term):
        return len(term) > 2
    return True


_QUERY_STOPWORDS = frozenset(
    {
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "has",
        "have",
        "had",
        "the",
        "and",
        "but",
        "not",
        "for",
        "from",
        "with",
        "without",
        "into",
        "onto",
        "off",
        "that",
        "this",
        "these",
        "those",
        "there",
        "here",
        "its",
        "their",
        "them",
        "they",
        "about",
        "any",
        "all",
        "some",
        "work",
        "works",
        "working",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "ein",
        "eine",
        "und",
        "oder",
        "nicht",
        "wie",
        "wer",
        "wann",
        "wo",
        "warum",
        "wieso",
        "welche",
        "welcher",
        "welches",
        "ist",
        "sind",
        "wird",
        "wurde",
        "hat",
        "haben",
        "kann",
        "koennen",
        "können",
        "soll",
        "muss",
        "sich",
        "bei",
        "mit",
        "von",
        "fuer",
        "für",
        "ueber",
        "über",
        "nach",
        "aus",
        "gibt",
        "es",
        "funktioniert",
        "geaendert",
        "geändert",
        "aendert",
        "ändert",
        "pourquoi",
        "quand",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "quoi",
        "qui",
        "que",
        "est",
        "sont",
        "fonctionne",
        "cette",
        "dans",
        "avec",
        "où",
        "cómo",
        "como",
        "qué",
        "cuál",
        "cuáles",
        "cuándo",
        "dónde",
        "donde",
        "porque",
        "por",
        "para",
        "funciona",
        "está",
        "están",
        "hay",
        "qual",
        "quais",
        "quando",
        "onde",
        "são",
        "estão",
        "tem",
        "uma",
        "não",
        "perché",
        "cosa",
        "quale",
        "quali",
        "dove",
        "funziona",
        "sono",
        "che",
        "della",
    }
)


def _query_terms(question: str) -> list[str]:
    """Split a query into searchable terms, segmenting Chinese text, then drop
    question/filler words (`_QUERY_STOPWORDS`, English plus common German/
    Romance-language fillers) so content words drive seeding. Falls back to the
    unfiltered terms if the query is all stopwords, so a question like "how does
    it work" or "wie funktioniert das" still seeds on something."""
    terms: list[str] = []
    for raw in question.split():
        if _has_chinese(raw):
            for seg in _segment_chinese(raw.lower().strip()):
                seg = seg.strip()
                if seg and _is_searchable(seg):
                    terms.append(seg)
        else:
            for tok in re.findall(r"\w+", raw.lower()):
                if _is_searchable(tok):
                    terms.append(tok)
    content = [t for t in terms if t not in _QUERY_STOPWORDS]
    return content or terms


_EXACT_MATCH_BONUS = 1000.0
_PREFIX_MATCH_BONUS = 100.0
_SUBSTRING_MATCH_BONUS = 1.0
_SOURCE_MATCH_BONUS = 0.5


def _compute_idf(G: nx.Graph, terms: list[str]) -> dict[str, float]:
    """IDF weights for query terms, cached in G.graph['_idf_cache'].

    Common terms like 'error' or 'exception' that match hundreds of nodes get
    low weights; rare identifiers like 'FooBarService' get high weights.
    Cache is stored on the graph object itself so it auto-invalidates when
    a hot-reload replaces G with a new object.
    """
    cache: dict[str, float] = G.graph.setdefault("_idf_cache", {})
    N = G.number_of_nodes() or 1
    uncached = [t for t in terms if t not in cache]
    if uncached:
        df: dict[str, int] = {t: 0 for t in uncached}
        for _, data in G.nodes(data=True):
            norm_label = (
                data.get("norm_label") or _strip_diacritics(data.get("label") or "")
            ).lower()
            for t in uncached:
                if t in norm_label:
                    df[t] += 1
        for t in uncached:
            cache[t] = math.log(1 + N / (1 + df[t]))
    return {t: cache.get(t, math.log(1 + N)) for t in terms}


def _trigrams(text: str) -> set[str]:
    """Character trigrams of `text`; for <3-char text the whole string is the key."""
    if len(text) < 3:
        return {text} if text else set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


def _node_search_text(data: dict, nid: str) -> str:
    """Concatenate every field _score_nodes / _find_node match a query against, so
    one trigram index over this text is a complete candidate generator for both.

    - `norm_label` and `source_file` feed _score_nodes' per-term substring tiers.
    - `label_tokens` (the space-joined token form) feeds _find_node's
      `term in label_tokens` branch, where a multi-word `term` can span a token
      boundary that punctuation hides in `norm_label` (e.g. query "foo bar" matches
      label "foo.bar" only via its tokenized form).
    - `source_tokens` feeds _find_node's exact source-file path lookup, where a
      query like "app/api/example/route.ts" tokenizes to "app api example route ts".
    - `nid` feeds the whole-query `joined == nid_lower` tier.

    NUL separators stop a trigram from spanning two fields (a query never contains
    NUL, so a cross-field trigram can never be a real match).
    """
    norm_label = data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
    label_tokens = " ".join(_search_tokens(data.get("label") or ""))
    source = (data.get("source_file") or "").lower()
    source_tokens = " ".join(_search_tokens(data.get("source_file") or ""))
    return "\x00".join((norm_label, label_tokens, str(nid).lower(), source, source_tokens))


def _get_trigram_index(G: nx.Graph) -> dict:
    """Lazily build and cache a trigram -> node-position postings map on the graph.

    Cached on `G.graph` so it auto-invalidates when a hot-reload swaps in a
    fresh graph object, exactly like `_idf_cache`. `set_cache` memoizes per-trigram
    id-sets across queries within one graph generation.
    """
    idx = G.graph.get("_trigram_index")
    if idx is not None:
        return idx
    ids = list(G.nodes())
    postings: dict[str, array] = {}
    for i, nid in enumerate(ids):
        for g in _trigrams(_node_search_text(G.nodes[nid], nid)):
            bucket = postings.get(g)
            if bucket is None:
                bucket = array("i")
                postings[g] = bucket
            bucket.append(i)
    idx = {"ids": ids, "postings": postings, "set_cache": {}}
    G.graph["_trigram_index"] = idx
    return idx


def _trigram_candidates(
    G: nx.Graph, needles: list[str], *, guard_frac: float = 0.10
) -> list[str] | None:
    """Node IDs whose text could contain any `needle` as a substring, via the
    trigram index — a *superset* the caller then re-scores with the exact predicates.

    Returns candidates in graph-iteration order (so order-sensitive callers like
    _find_node stay byte-identical to a full scan), or **None** when the index isn't
    worth it — a needle is too short to trigram, or its rarest trigram is still
    common enough that the candidate set would approach the whole graph. The caller
    falls back to the full scan, preserving the never-worse contract. The guard is
    cheap: postings-length lookups only, no set intersection.
    """
    idx = _get_trigram_index(G)
    ids, postings, set_cache = idx["ids"], idx["postings"], idx["set_cache"]
    n = len(ids)
    if n == 0:
        return []
    needles = [s for s in needles if s]
    thresh = int(n * guard_frac)
    for s in needles:
        tgs = _trigrams(s)
        if not tgs or any(len(g) < 3 for g in tgs):
            return None
        present = [len(postings[g]) for g in tgs if g in postings]
        if not present:
            continue
        if min(present) > thresh:
            return None
    cand: set[int] = set()
    for s in needles:
        sets: list[set] | None = []
        for g in _trigrams(s):
            bucket = postings.get(g)
            if bucket is None:
                sets = None
                break
            cached = set_cache.get(g)
            if cached is None:
                cached = set(bucket)
                set_cache[g] = cached
            sets.append(cached)
        if not sets:
            continue
        sets.sort(key=len)
        hit = set(sets[0])
        for other in sets[1:]:
            hit &= other
            if not hit:
                break
        cand |= hit
    return [ids[i] for i in sorted(cand)]


class _QueryScores(NamedTuple):
    """Per-query scoring result, returned by the private `_score_query` helper.

    `ranked` is the existing ordered `(score, node_id)` ranking produced by the
    combined query scorer (the value `_score_nodes` always returned). When the
    caller asks for it via `collect_per_term_seeds=True`, `best_seed_by_term`
    additionally carries the winning node id for each normalized search token —
    the seed `_pick_seeds` would have picked for that token via the now-retired
    per-token `_score_nodes([token])` rescoring pass — computed in the *same*
    per-node traversal so the query path makes exactly one graph scoring pass
    regardless of query length. Empty when `collect_per_term_seeds=False`.
    """

    ranked: list[tuple[float, str]]
    best_seed_by_term: dict[str, str]


def _score_nodes(G: nx.Graph, terms: list[str]) -> list[tuple[float, str]]:
    """Combined query scorer returning the existing ranked `(score, node_id)` list.

    Backwards-compatible thin wrapper around `_score_query` for path, explain,
    tests, and every other caller that only needs the combined ranking. The
    per-term seed metadata computed by `_score_query` (when requested) is
    discarded here so existing callers see no API or runtime-cost change.
    """
    return _score_query(G, terms, collect_per_term_seeds=False).ranked


def _score_query(G: nx.Graph, terms: list[str], *, collect_per_term_seeds: bool) -> _QueryScores:
    """Single-pass combined scorer that optionally also records the best seed
    for each normalized query token.

    The combined ranking is byte-identical to what `_score_nodes` produced
    before the refactor; `_score_nodes` is now a thin wrapper that asks for
    `collect_per_term_seeds=False` and returns only `.ranked`.

    When `collect_per_term_seeds=True`, the per-token singleton winner is
    computed alongside the combined score in the *same* per-node visit (it
    reuses the same `norm_label` / `label_tokens` / `source` already evaluated
    for the combined tier), so `_query_graph_text` can feed `best_seed_by_term`
    straight into `_pick_seeds` and skip the T additional whole-graph rescoring
    passes the old per-token `_score_nodes([token])` loop ran.

    Singleton-winner semantics match the legacy per-token path exactly. The
    score itself mirrors `_score_nodes([token])` with `n_terms == 1` (so the
    coverage term is 1 and the per-token tier is unscaled) plus the broader
    joined-singlet tier (which also checks `label_tokens` and `nid_lower`).
    Tie-break order is (1) highest singleton score, (2) highest graph degree,
    (3) shortest displayed label, (4) lexicographically smallest node id —
    exactly what `max(tied, key=degree)` over a sort by `(-score, label_len,
    nid)` produced in the legacy `_pick_seeds` per-token loop. The combined
    trigram candidate set (needles `norm_terms + [joined]`) is a superset of
    each per-token `[t]` candidate set, so iterating combined candidates
    discovers every non-zero singleton-score node for every term.
    """
    scored: list[tuple[float, str]] = []
    norm_terms = list(dict.fromkeys(tok for t in terms for tok in _search_tokens(t)))
    n_terms = len(norm_terms)
    idf = _compute_idf(G, norm_terms)
    joined = " ".join(norm_terms)
    joined_w = max((idf.get(t, 1.0) for t in norm_terms), default=1.0)
    candidate_ids = _trigram_candidates(G, norm_terms + ([joined] if joined else []))
    node_iter = (
        G.nodes(data=True)
        if candidate_ids is None
        else ((nid, G.nodes[nid]) for nid in candidate_ids)
    )
    best_by_term: dict[str, tuple[tuple, str]] | None = {} if collect_per_term_seeds else None
    for nid, data in node_iter:
        norm_label = data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
        bare_label = norm_label.rstrip("()")
        label_tokens = " ".join(_search_tokens(data.get("label") or ""))
        source = (data.get("source_file") or "").lower()
        nid_lower = nid.lower() if (joined or collect_per_term_seeds) else ""
        score = 0.0
        if joined:
            if joined in (norm_label, bare_label, label_tokens, nid_lower):
                score += _EXACT_MATCH_BONUS * 10 * joined_w
            elif (
                norm_label.startswith(joined)
                or bare_label.startswith(joined)
                or label_tokens.startswith(joined)
            ):
                score += _PREFIX_MATCH_BONUS * 10 * joined_w
        matched = 0
        tiered = 0.0
        for t in norm_terms:
            w = idf.get(t, 1.0)
            tier_value = 0.0
            substr_value = 0.0
            source_value = 0.0
            if t == norm_label or t == bare_label:
                tier_value = _EXACT_MATCH_BONUS * w
                matched += 1
            elif norm_label.startswith(t) or bare_label.startswith(t):
                tier_value = _PREFIX_MATCH_BONUS * w
                matched += 1
            elif t in norm_label:
                substr_value = _SUBSTRING_MATCH_BONUS * w
                score += substr_value
                matched += 1
            if t in source:
                source_value = _SOURCE_MATCH_BONUS * w
                score += source_value
            tiered += tier_value
            if collect_per_term_seeds and best_by_term is not None:
                if t in (norm_label, bare_label, label_tokens, nid_lower):
                    singleton = _EXACT_MATCH_BONUS * 10 * w
                elif (
                    norm_label.startswith(t)
                    or bare_label.startswith(t)
                    or label_tokens.startswith(t)
                ):
                    singleton = _PREFIX_MATCH_BONUS * 10 * w
                else:
                    singleton = 0.0
                singleton += tier_value + substr_value + source_value
                if singleton > 0:
                    key = (-singleton, -G.degree(nid), len(data.get("label") or nid), nid)
                    cur = best_by_term.get(t)
                    if cur is None or key < cur[0]:
                        best_by_term[t] = (key, nid)
        if tiered:
            score += tiered * (matched / n_terms) ** 2
        if score > 0:
            scored.append((score, nid))
    scored.sort(key=lambda s: (-s[0], len(G.nodes[s[1]].get("label") or s[1]), s[1]))
    best_seed_by_term: dict[str, str] = {}
    if collect_per_term_seeds and best_by_term:
        best_seed_by_term = {t: nid for t, (_key, nid) in best_by_term.items()}
    return _QueryScores(ranked=scored, best_seed_by_term=best_seed_by_term)


def _pick_scored_endpoint(G: nx.Graph, scored: list[tuple[float, str]], query: str) -> str:
    """Pick a path endpoint from a _score_nodes result, preferring full-token matches.

    The full-query tier in _score_nodes only fires when the query equals or
    prefixes a label, so a query that is a token *subset* of the intended label
    (query "Reject-everything judge" vs. label "Degenerate Reject-Everything
    Judge") gets no bonus, and a node prefix-matching one rare token (label
    "Rejection Summary") can out-score it on IDF alone. Committing to scored[0]
    then anchors the path on an unrelated — often disconnected — node and yields
    a false "No path found". Scan the score-ordered list and take the first
    candidate whose label contains EVERY query token; when the top candidate
    already full-matches, or no candidate does, this is exactly scored[0].

    `scored` must be non-empty (both callers return early on no match).
    """
    qtokens = set(_search_tokens(query))
    if not qtokens:
        return scored[0][1]
    for _score, nid in scored:
        if qtokens <= set(_search_tokens(G.nodes[nid].get("label") or nid)):
            return nid
    return scored[0][1]


def _pick_seeds(
    scored: list[tuple[float, str]],
    max_k: int = 3,
    gap_ratio: float = 0.2,
    *,
    G: "nx.Graph | None" = None,
    best_seed_by_term: dict[str, str] | None = None,
) -> list[str]:
    """Select BFS seed nodes, stopping when score drops too far below the top.

    Prevents high-frequency noise terms (error, exception) from stealing seed
    slots from a dominant identifier match. When FooBarService scores 1000 and
    error nodes score 1.0, only FooBarService is seeded — the score gap is 99.9%
    which is well above the 20% threshold that would allow additional seeds.

    That same gap_ratio cutoff has a failure mode on multi-term natural-language
    queries: if one term happens to hit an EXACT label match on a node that is
    otherwise unrelated to the query's intent (e.g. a common word that is also
    used as an unrelated identifier or field name elsewhere in the corpus), it
    can outscore every SUBSTRING match on the query's other, actually-relevant
    terms by ~1000x (see `_EXACT_MATCH_BONUS` vs. `_SUBSTRING_MATCH_BONUS`).
    The 20%-gap cutoff then silently discards all of those substring-tier
    seeds, so the BFS traversal only ever explores the neighborhood of the one
    unrelated exact match — see #1445.

    When `G` and `best_seed_by_term` are supplied, this guarantees at least one
    seed per distinct query term that has any match at all, so one term's
    incidental collision cannot starve out the others. The per-token winners
    in `best_seed_by_term` are precomputed by `_score_query` (during the same
    traversal that produced `scored`) so this function no longer rescores the
    graph per term — see #1445 and the `_score_query` docstring.

    Coverage scaling in _score_nodes (#1602) now dampens a lone collision's
    exact tier on multi-term queries, which brings label-matching relevant
    nodes back inside the gap window; this per-term guarantee remains
    load-bearing for relevant nodes matched only via substrings, whose flat
    scores a dampened collision can still exceed.
    """
    if not scored:
        return []

    def _seed_label_key(nid: str) -> str:
        if G is None:
            return nid
        data = G.nodes[nid]
        return (data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()) or nid

    top_score = scored[0][0]
    seeds: list[str] = []
    seen_labels: set[str] = set()
    for score, nid in scored:
        if len(seeds) >= max_k:
            break
        if seeds and score < top_score * gap_ratio:
            break
        key = _seed_label_key(nid)
        if key in seen_labels:
            continue
        seen_labels.add(key)
        seeds.append(nid)

    if G is not None and best_seed_by_term:
        for term in sorted(best_seed_by_term):
            best_nid = best_seed_by_term[term]
            key = _seed_label_key(best_nid)
            if best_nid not in seeds and key not in seen_labels:
                seen_labels.add(key)
                seeds.append(best_nid)
    return seeds


_CONTEXT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("call", ("call", "calls", "called", "invoke", "invokes", "invoked")),
    ("import", ("import", "imports", "imported", "module", "modules")),
    ("field", ("field", "fields", "member", "members", "property", "properties")),
    ("parameter_type", ("parameter", "parameters", "param", "params", "argument", "arguments")),
    ("return_type", ("return", "returns", "returned")),
    ("generic_arg", ("generic", "generics", "template", "templates")),
)


_CONTEXT_FILTER_ALIASES: dict[str, str] = {
    "param": "parameter_type",
    "params": "parameter_type",
    "parameter": "parameter_type",
    "parameters": "parameter_type",
    "argument": "parameter_type",
    "arguments": "parameter_type",
    "arg": "parameter_type",
    "args": "parameter_type",
    "return": "return_type",
    "returns": "return_type",
    "returned": "return_type",
    "generic": "generic_arg",
    "generics": "generic_arg",
    "template": "generic_arg",
    "templates": "generic_arg",
    "annotation": "attribute",
    "annotations": "attribute",
    "decorator": "attribute",
    "decorators": "attribute",
    "calls": "call",
    "called": "call",
    "invoke": "call",
    "invocation": "call",
    "fields": "field",
    "property": "field",
    "properties": "field",
    "member": "field",
    "members": "field",
    "imports": "import",
    "imported": "import",
    "module": "import",
    "modules": "import",
    "exports": "export",
    "exported": "export",
}


def _normalize_context_filters(filters: list[str] | None) -> list[str]:
    if not filters:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in filters:
        key = _strip_diacritics(str(value)).strip().lower()
        if not key:
            continue
        key = _CONTEXT_FILTER_ALIASES.get(key, key)
        if key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized


def _infer_context_filters(question: str) -> list[str]:
    lowered = {
        _strip_diacritics(token).lower()
        for token in question.replace("?", " ").replace(",", " ").split()
    }
    inferred: list[str] = []
    for context, hints in _CONTEXT_HINTS:
        if any(hint in lowered for hint in hints):
            inferred.append(context)
    return inferred


def _resolve_context_filters(
    question: str, explicit_filters: list[str] | None = None
) -> tuple[list[str], str | None]:
    normalized = _normalize_context_filters(explicit_filters)
    if normalized:
        return normalized, "explicit"
    inferred = _infer_context_filters(question)
    if inferred:
        return inferred, "heuristic"
    return [], None


def _filter_graph_by_context(G: nx.Graph, context_filters: list[str] | None) -> nx.Graph:
    filters = set(_normalize_context_filters(context_filters))
    if not filters:
        return G
    H = G.__class__()
    H.add_nodes_from(G.nodes(data=True))
    if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)):
        for u, v, key, data in G.edges(keys=True, data=True):
            if data.get("context") in filters:
                H.add_edge(u, v, key=key, **data)
    else:
        for u, v, data in G.edges(data=True):
            if data.get("context") in filters:
                H.add_edge(u, v, **data)
    return H


def _bfs(
    G: nx.Graph,
    start_nodes: list[str],
    depth: int,
    *,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> tuple[set[str], list[tuple]]:
    result = expand_neighborhood(
        G,
        start_nodes,
        depth,
        mode="bfs",
        weighted=weighted,
        registry=registry,
    )
    return result.nodes, result.edges


def _dfs(
    G: nx.Graph,
    start_nodes: list[str],
    depth: int,
    *,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> tuple[set[str], list[tuple]]:
    result = expand_neighborhood(
        G,
        start_nodes,
        depth,
        mode="dfs",
        weighted=weighted,
        registry=registry,
    )
    return result.nodes, result.edges


def _subgraph_to_text(
    G: nx.Graph,
    nodes: set[str],
    edges: list[tuple],
    token_budget: int = 2000,
    *,
    seeds: list[str] | None = None,
    weighted: bool = True,
    reach_importance: dict[str, float] | None = None,
    distance: dict[str, int] | None = None,
    registry: RelationWeightRegistry | None = None,
) -> str:
    """Render subgraph as text, cutting at token_budget (approx 3 chars/token).

    seeds: exact-match nodes rendered first before the degree-sorted expansion,
    so the queried symbol always appears at the top of the output.
    """
    char_budget = token_budget * 3
    lines = []
    overlay = getattr(G, "graph", {}).get("_learning_overlay", {}) or {}
    seed_hits = [n for n in (seeds or []) if n in nodes]

    def _adj(n):
        if G.is_directed():
            yield from G.successors(n)
            yield from G.predecessors(n)
        else:
            yield from G.neighbors(n)

    if distance is None:
        dist: dict[str, int] = {n: 0 for n in seed_hits}
        frontier, hop = seed_hits, 0
        while frontier:
            hop += 1
            nxt = []
            for n in frontier:
                for nb in _adj(n):
                    if nb in nodes and nb not in dist:
                        dist[nb] = hop
                        nxt.append(nb)
            frontier = nxt
    else:
        dist = distance

    ordered = rank_nodes_for_retrieval(
        nodes,
        seeds=seeds,
        distance=dist,
        reach_importance=reach_importance or {},
        degree_of=G.degree,
        weighted=weighted,
    )
    render_edges = rank_edges_for_retrieval(list(edges), G, weighted=weighted, registry=registry)
    for nid in ordered:
        d = G.nodes[nid]
        entry = overlay.get(str(nid))
        learning_suffix = ""
        if entry:
            status = sanitize_label(str(entry.get("status", "")))
            if status:
                learning_suffix = f" learning={status}{':stale' if entry.get('stale') else ''}"
        line = (
            f"NODE {sanitize_label(d.get('label', nid))} "
            f"[src={sanitize_label(str(d.get('source_file', '')))} "
            f"loc={sanitize_label(str(d.get('source_location', '')))} "
            f"community={sanitize_label(str(d.get('community_name') or d.get('community', '')))}"
            f"{learning_suffix}]"
        )
        lines.append(line)
    for u, v in render_edges:
        if u not in nodes or v not in nodes:
            continue
        if G.has_edge(u, v):
            raw = G[u][v]
            forward = True
        elif G.has_edge(v, u):
            raw = G[v][u]
            forward = False
        else:
            continue
        d = next(iter(raw.values()), {}) if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)) else raw
        if forward:
            src = d.get("_src", u)
            tgt = d.get("_tgt", v)
            if {src, tgt} != {u, v}:
                src, tgt = u, v
        else:
            src = d.get("_src", v)
            tgt = d.get("_tgt", u)
            if {src, tgt} != {u, v}:
                src, tgt = v, u
        context = d.get("context")
        context_suffix = f" context={sanitize_label(str(context))}" if context else ""
        _loc = str(d.get("source_location") or "")
        at_suffix = (
            f" at={sanitize_label(str(d.get('source_file') or ''))}:{sanitize_label(_loc)}"
            if _loc
            else ""
        )
        line = (
            f"EDGE {sanitize_label(G.nodes[src].get('label', src))} "
            f"--{sanitize_label(str(d.get('relation', '')))} "
            f"[{sanitize_label(str(d.get('confidence', '')))}{context_suffix}]--> "
            f"{sanitize_label(G.nodes[tgt].get('label', tgt))}{at_suffix}"
        )
        lines.append(line)
    output = "\n".join(lines)
    if len(output) > char_budget:
        cut_at = output[:char_budget].rfind("\n")
        cut_at = cut_at if cut_at > 0 else char_budget
        if seed_hits:
            seed_block_end = sum(len(lines[i]) + 1 for i in range(len(seed_hits))) - 1
            cut_at = max(cut_at, min(seed_block_end, len(output)))
        total_nodes = sum(1 for l in lines if l.startswith("NODE "))
        shown_nodes = output[:cut_at].count("\nNODE ") + (1 if output.startswith("NODE ") else 0)
        cut_count = total_nodes - shown_nodes
        output = (
            f"[!] TRUNCATED: showing {shown_nodes} of {total_nodes} nodes "
            f"(~{token_budget}-token budget). The answer may be among the "
            f"{cut_count} cut nodes — raise the token budget (CLI: --budget) or "
            f"narrow the query (e.g. context_filter=['call'], or get_node for a "
            f"specific symbol).\n\n"
            + output[:cut_at]
            + f"\n... (truncated — {cut_count} more nodes cut by ~{token_budget}-token budget."
            f" Narrow with context_filter=['call'] or use get_node for a specific symbol)"
        )
    return output


def _cut_lines_to_budget(lines: list[str], token_budget: int, narrow_hint: str) -> str:
    """Render pre-built lines under the same ~3-chars/token budget rule as
    _subgraph_to_text; over-budget output is cut at a line boundary with a count and a
    narrowing hint instead of flooding the caller's context window."""
    output = "\n".join(lines)
    char_budget = token_budget * 3
    if len(output) <= char_budget:
        return output
    cut_at = output[:char_budget].rfind("\n")
    cut_at = cut_at if cut_at > 0 else char_budget
    kept = output[:cut_at]
    shown = kept.count("\n") + 1
    cut_count = len(lines) - shown
    return (
        f"[!] TRUNCATED: showing {shown} of {len(lines)} lines "
        f"(~{token_budget}-token budget). {narrow_hint}\n\n"
        + kept
        + f"\n... (truncated — {cut_count} more lines cut by ~{token_budget}-token budget. "
        + narrow_hint
        + ")"
    )


def _query_graph_text(
    G: nx.Graph,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = 3,
    token_budget: int = 2000,
    context_filters: list[str] | None = None,
    weighted: bool = True,
    registry: RelationWeightRegistry | None = None,
    community_aware: bool = False,
    max_communities: int = 2,
    community_min_confidence: float = 0.55,
) -> str:
    terms = _query_terms(question)
    qs = _score_query(G, terms, collect_per_term_seeds=True)
    start_nodes = _pick_seeds(qs.ranked, G=G, best_seed_by_term=qs.best_seed_by_term)
    if not start_nodes:
        return "No matching nodes found."
    resolved_filters, filter_source = _resolve_context_filters(question, context_filters)
    traversal_graph = _filter_graph_by_context(G, resolved_filters)
    traversal_mode = "dfs" if mode == "dfs" else "bfs"
    selection: CommunitySelection | None = None
    if community_aware:
        traversal, selection, traversal_graph = community_aware_expand(
            traversal_graph,
            start_nodes,
            depth,
            mode=traversal_mode,
            weighted=weighted,
            registry=registry,
            query_terms=terms,
            community_aware=True,
            max_communities=max_communities,
            min_confidence=community_min_confidence,
        )
    else:
        traversal = expand_neighborhood(
            traversal_graph,
            start_nodes,
            depth,
            mode=traversal_mode,
            weighted=weighted,
            registry=registry,
        )
    nodes, edges = traversal.nodes, traversal.edges
    header_parts = [
        f"Traversal: {mode.upper()} depth={depth}",
        f"Start: {[G.nodes[n].get('label', n) for n in start_nodes]}",
    ]
    if weighted:
        header_parts.insert(1, "Weighted relations")
    if selection is not None and selection.selected:
        header_parts.insert(
            1 if not weighted else 2,
            f"Communities={list(selection.community_ids)} (confidence={selection.confidence:.2f})",
        )
    elif selection is not None and community_aware:
        header_parts.insert(
            1 if not weighted else 2,
            f"Community fallback ({selection.reason})",
        )
    if resolved_filters:
        header_parts.append(f"Context: {', '.join(resolved_filters)} ({filter_source})")
    header_parts.append(f"{len(nodes)} nodes found")
    header = " | ".join(header_parts) + "\n\n"
    return header + _subgraph_to_text(
        traversal_graph,
        nodes,
        edges,
        token_budget,
        seeds=start_nodes,
        weighted=weighted,
        reach_importance=traversal.reach_importance,
        distance=traversal.distance,
        registry=registry,
    )


def _find_node(G: nx.Graph, label: str) -> list[str]:
    """Return node IDs whose label or ID matches the search term (diacritic-insensitive).

    Results are ordered by precedence: exact source-file path match first, then
    exact (label/ID) match, then prefix match, then substring match. Node-ID exact
    matches are grouped with label exact matches.
    """
    term = " ".join(_search_tokens(label))
    if not term:
        return []
    norm_query = _strip_diacritics(str(label)).lower().strip()
    source_exact: list[str] = []
    exact: list[str] = []
    prefix: list[str] = []
    substring: list[str] = []
    candidate_ids = _trigram_candidates(G, [term, norm_query])
    node_iter = (
        G.nodes(data=True)
        if candidate_ids is None
        else ((nid, G.nodes[nid]) for nid in candidate_ids)
    )
    for nid, d in node_iter:
        norm_label = d.get("norm_label") or _strip_diacritics(d.get("label") or "").lower()
        bare_label = norm_label.rstrip("()")
        label_tokens = " ".join(_search_tokens(d.get("label") or ""))
        source_tokens = " ".join(_search_tokens(d.get("source_file") or ""))
        nid_lower = nid.lower()
        if term == source_tokens:
            source_exact.append(nid)
        elif (
            term == norm_label
            or term == bare_label
            or term == label_tokens
            or term == nid_lower
            or norm_query == norm_label
            or norm_query == bare_label
        ):
            exact.append(nid)
        elif (
            norm_label.startswith(term)
            or bare_label.startswith(term)
            or label_tokens.startswith(term)
            or nid_lower.startswith(term)
            or norm_label.startswith(norm_query)
            or bare_label.startswith(norm_query)
        ):
            prefix.append(nid)
        elif term in norm_label or term in label_tokens or norm_query in norm_label:
            substring.append(nid)

    if source_exact:
        query_basename = _strip_diacritics(Path(label).name).lower()
        preferred = []
        for nid in source_exact:
            if str(G.nodes[nid].get("source_location", "")) != "L1":
                continue
            lbl = _strip_diacritics(str(G.nodes[nid].get("label") or "")).lower()
            if lbl == query_basename or lbl.endswith("/" + query_basename):
                preferred.append(nid)
        if len(preferred) == 1:
            source_exact = preferred + [nid for nid in source_exact if nid != preferred[0]]

    return source_exact + exact + prefix + substring


def _filter_blank_stdin() -> None:
    """Filter blank lines from stdin before MCP reads it.

    Some MCP clients (Claude Desktop, etc.) send blank lines between JSON
    messages. The MCP stdio transport tries to parse every line as a
    JSONRPCMessage, so a bare newline triggers a Pydantic ValidationError.
    This installs an OS-level pipe that relays stdin while dropping blanks.
    """
    import os
    import threading

    r_fd, w_fd = os.pipe()
    saved_fd = os.dup(sys.stdin.fileno())

    def _relay() -> None:
        try:
            with open(saved_fd, "rb") as src, open(w_fd, "wb") as dst:
                for line in src:
                    if line.strip():
                        dst.write(line)
                        dst.flush()
        except Exception:
            pass

    threading.Thread(target=_relay, daemon=True).start()
    os.dup2(r_fd, sys.stdin.fileno())
    os.close(r_fd)
    sys.stdin = open(0, "r", closefd=False)


def _community_header(cid: int, community_name) -> str:
    base = f"Community {cid}"
    if community_name:
        clean = sanitize_label(str(community_name))
        if clean and clean != base:
            return f"{base} — {clean}"
    return base


def _build_server(graph_path: str):
    """Build the configured low-level MCP Server (shared by every transport).

    All graph query tools and resources are registered here over a single
    ``mcp.server.Server`` instance; the caller picks the transport (stdio or
    Streamable HTTP) and runs it. Hot-reload of graph.json works the same way
    regardless of transport, since reloads happen inside the tool handlers.
    """
    import threading

    try:
        from mcp.server import Server
        from mcp import types
        from mcp.types import AnyUrl
    except ImportError as e:
        raise ImportError('mcp not installed. Run: pip install "graphifyy[mcp]"') from e

    from graphify import paths as _paths

    _default_graph_path = graph_path
    _ctx_lock = threading.Lock()
    _ctx_cache: dict[str, dict] = {}

    def _load_ctx(path: str):
        """Return (G, communities) for a graph.json path, reusing a cached
        context until the file's (mtime, size) changes and then transparently
        rebuilding it. Unlike ``_load_graph`` it never exits the process on a
        missing/corrupt file — it raises, so a bad project_path surfaces as a
        tool error instead of killing a server that is happily serving other
        projects."""
        try:
            s = Path(path).stat()
            key = (s.st_mtime_ns, s.st_size)
        except FileNotFoundError:
            raise FileNotFoundError(f"graph.json not found: {path}")
        ent = _ctx_cache.get(path)
        if ent is not None and ent["key"] == key:
            return ent["G"], ent["communities"]
        with _ctx_lock:
            ent = _ctx_cache.get(path)
            if ent is not None and ent["key"] == key:
                return ent["G"], ent["communities"]
            try:
                new_G = _load_graph(path)
            except SystemExit as e:
                raise RuntimeError(f"could not load graph.json at {path}") from e
            _get_trigram_index(new_G)
            comm = _communities_from_graph(new_G)
            _ctx_cache[path] = {"key": key, "G": new_G, "communities": comm}
            return new_G, comm

    def _resolve_graph_path(project_path) -> str:
        """Map an optional project_path to a concrete graph.json path. ``None``
        keeps the server's default graph (backward-compatible); a project_path
        resolves to ``<project_path>/<GRAPHIFY_OUT>/graph.json``, honouring the
        GRAPHIFY_OUT override so worktree/shared-output setups keep working."""
        if not project_path:
            return _default_graph_path
        return str(Path(project_path) / _paths.GRAPHIFY_OUT / "graph.json")

    active_graph_path = _default_graph_path
    try:
        G, communities = _load_ctx(_default_graph_path)
    except (FileNotFoundError, RuntimeError):
        G, communities = None, {}

    def _select_graph(project_path) -> None:
        nonlocal G, communities, active_graph_path
        path = _resolve_graph_path(project_path)
        G, communities = _load_ctx(path)
        active_graph_path = path

    server = Server("graphify")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        _tools = [
            types.Tool(
                name="query_graph",
                description="Search the knowledge graph using BFS or DFS. Returns relevant nodes and edges as text context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Natural language question or keyword search",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["bfs", "dfs"],
                            "default": "bfs",
                            "description": "bfs=broad context, dfs=trace a specific path",
                        },
                        "depth": {
                            "type": "integer",
                            "default": 3,
                            "description": "Traversal depth (1-6)",
                        },
                        "token_budget": {
                            "type": "integer",
                            "default": 2000,
                            "description": "Max output tokens",
                        },
                        "context_filter": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional explicit edge-context filter, e.g. ['call', 'field']",
                        },
                        "weighted": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Prefer semantically stronger relations "
                                "(calls > inherits > implements > references > imports) "
                                "during traversal and result ranking"
                            ),
                        },
                        "community_aware": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Restrict retrieval to the most relevant communities "
                                "before weighted neighborhood expansion; falls back to "
                                "full-graph retrieval when community confidence is low"
                            ),
                        },
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="get_node",
                description="Get full details for a specific node by label or ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Node label or ID to look up"}
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="get_neighbors",
                description="Get all direct neighbors of a node with edge details.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "relation_filter": {
                            "type": "string",
                            "description": "Optional: filter by relation type",
                        },
                        "token_budget": {
                            "type": "integer",
                            "default": 2000,
                            "description": "Max output tokens",
                        },
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="get_community",
                description="Get all nodes in a community by community ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "community_id": {
                            "type": "integer",
                            "description": "Community ID (0-indexed by size)",
                        },
                        "token_budget": {
                            "type": "integer",
                            "default": 2000,
                            "description": "Max output tokens",
                        },
                    },
                    "required": ["community_id"],
                },
            ),
            types.Tool(
                name="god_nodes",
                description="Return the most connected nodes - the core abstractions of the knowledge graph.",
                inputSchema={
                    "type": "object",
                    "properties": {"top_n": {"type": "integer", "default": 10}},
                },
            ),
            types.Tool(
                name="graph_stats",
                description="Return summary statistics: node count, edge count, communities, confidence breakdown.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="shortest_path",
                description="Find the shortest path between two concepts in the knowledge graph.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Source concept label or keyword",
                        },
                        "target": {
                            "type": "string",
                            "description": "Target concept label or keyword",
                        },
                        "max_hops": {
                            "type": "integer",
                            "default": 8,
                            "description": "Maximum hops to consider",
                        },
                        "weighted": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Use relationship-aware edge costs "
                                "(prefer calls/inherits over imports)"
                            ),
                        },
                    },
                    "required": ["source", "target"],
                },
            ),
            types.Tool(
                name="list_prs",
                description=(
                    "List open GitHub PRs with CI status, review state, and graph impact "
                    "(which communities each PR touches, blast radius). Use this before starting "
                    "work to check if a PR already covers the area you're about to change."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {
                            "type": "string",
                            "description": "Base branch to filter PRs by (auto-detected if omitted)",
                        },
                        "repo": {
                            "type": "string",
                            "description": "GitHub repo (owner/repo). Defaults to current repo.",
                        },
                    },
                },
            ),
            types.Tool(
                name="get_pr_impact",
                description=(
                    "Get detailed graph impact for a specific PR: which files it changes, "
                    "which knowledge-graph communities are affected, and how many nodes are touched. "
                    "Use this to assess merge risk or check for overlap with your current work."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pr_number": {"type": "integer", "description": "PR number to analyse"},
                        "repo": {
                            "type": "string",
                            "description": "GitHub repo (owner/repo). Defaults to current repo.",
                        },
                    },
                    "required": ["pr_number"],
                },
            ),
            types.Tool(
                name="triage_prs",
                description=(
                    "Return all actionable open PRs (correct base, not stale) with full graph impact data "
                    "so you can reason about review priority, merge order, and conflict risk. "
                    "Call this when the user asks 'what PRs should I review?' or 'what's ready to merge?'"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {
                            "type": "string",
                            "description": "Base branch to filter PRs by (auto-detected if omitted)",
                        },
                        "repo": {
                            "type": "string",
                            "description": "GitHub repo (owner/repo). Defaults to current repo.",
                        },
                    },
                },
            ),
            types.Tool(
                name="reason_with_graph",
                description=(
                    "Bidirectional LLM↔graph reasoning (Synapse C2): retrieve graph context, "
                    "ask the LLM for structured claims, validate claims against the graph, "
                    "revise if needed, and return the final answer with evidence."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Question to answer using the knowledge graph",
                        },
                        "revise": {
                            "type": "boolean",
                            "default": True,
                            "description": "Re-prompt the LLM when validation finds issues",
                        },
                        "community_aware": {
                            "type": "boolean",
                            "default": True,
                            "description": "Use community-scoped retrieval when confident",
                        },
                        "weighted": {
                            "type": "boolean",
                            "default": True,
                            "description": "Prefer stronger relations during retrieval",
                        },
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="validate_proposal",
                description=(
                    "Pre-execution hallucination check (Synapse C3): validate proposed "
                    "Python code or a tool-call payload against the repository graph "
                    "before execution. Returns VALID / INVALID / UNKNOWN with evidence."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Proposed source code to validate",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Optional tool name (Write/Edit/Bash) when validating a tool payload",
                        },
                        "tool_input": {
                            "type": "object",
                            "description": "Optional tool_input dict (content/new_string/file_path/command)",
                        },
                        "strict": {
                            "type": "boolean",
                            "default": False,
                            "description": "If true, mark blocked=true on INVALID/CONTRADICTED",
                        },
                    },
                },
            ),
        ]
        for _t in _tools:
            _t.inputSchema.setdefault("properties", {})["project_path"] = {
                "type": "string",
                "description": (
                    "Absolute path to a project directory containing "
                    "graphify-out/graph.json. Optional — defaults to the graph "
                    "this server was started with."
                ),
            }
        return _tools

    def _tool_query_graph(arguments: dict) -> str:
        import time as _time
        from graphify import querylog

        question = arguments["question"]
        mode = arguments.get("mode", "bfs")
        depth = min(int(arguments.get("depth", 3)), 6)
        budget = int(arguments.get("token_budget", 2000))
        context_filter = arguments.get("context_filter")
        weighted = bool(arguments.get("weighted", True))
        community_aware = bool(arguments.get("community_aware", False))
        _t0 = _time.perf_counter()
        result = _query_graph_text(
            G,
            question,
            mode=mode,
            depth=depth,
            token_budget=budget,
            context_filters=context_filter,
            weighted=weighted,
            community_aware=community_aware,
        )
        querylog.log_query(
            kind="mcp_query",
            question=question,
            corpus=str(active_graph_path),
            result=result,
            mode=mode,
            depth=depth,
            token_budget=budget,
            duration_ms=(_time.perf_counter() - _t0) * 1000,
        )
        return result

    def _tool_get_node(arguments: dict) -> str:
        label = arguments["label"].lower()
        matches = [
            (nid, d)
            for nid, d in G.nodes(data=True)
            if label in (d.get("label") or "").lower() or label == nid.lower()
        ]
        if not matches:
            return f"No node matching '{label}' found."
        nid, d = matches[0]
        return "\n".join(
            [
                f"Node: {sanitize_label(d.get('label', nid))}",
                f"  ID: {sanitize_label(nid)}",
                f"  Source: {sanitize_label(str(d.get('source_file', '')))} {sanitize_label(str(d.get('source_location', '')))}",
                f"  Type: {sanitize_label(str(d.get('file_type', '')))}",
                f"  Community: {sanitize_label(str(d.get('community_name') or d.get('community', '')))}",
                f"  Degree: {G.degree(nid)}",
            ]
        )

    def _tool_get_neighbors(arguments: dict) -> str:
        label = arguments["label"].lower()
        rel_filter = arguments.get("relation_filter", "").lower()
        matches = _find_node(G, label)
        if not matches:
            return f"No node matching '{label}' found."
        nid = matches[0]
        lines = [f"Neighbors of {sanitize_label(G.nodes[nid].get('label', nid))}:"]

        def _edge_at(d: dict) -> str:
            loc = str(d.get("source_location") or "")
            return (
                f" at={sanitize_label(str(d.get('source_file') or ''))}:{sanitize_label(loc)}"
                if loc
                else ""
            )

        for nb in G.successors(nid):
            d = edge_data(G, nid, nb)
            rel = d.get("relation", "")
            if rel_filter and rel_filter not in rel.lower():
                continue
            lines.append(
                f"  --> {sanitize_label(G.nodes[nb].get('label', nb))} "
                f"[{sanitize_label(str(rel))}] [{sanitize_label(str(d.get('confidence', '')))}]{_edge_at(d)}"
            )
        for nb in G.predecessors(nid):
            d = edge_data(G, nb, nid)
            rel = d.get("relation", "")
            if rel_filter and rel_filter not in rel.lower():
                continue
            lines.append(
                f"  <-- {sanitize_label(G.nodes[nb].get('label', nb))} "
                f"[{sanitize_label(str(rel))}] [{sanitize_label(str(d.get('confidence', '')))}]{_edge_at(d)}"
            )
        budget = int(arguments.get("token_budget", 2000))
        return _cut_lines_to_budget(
            lines, budget, "Narrow with relation_filter or use get_node for a specific symbol"
        )

    def _tool_get_community(arguments: dict) -> str:
        cid = int(arguments["community_id"])
        nodes = communities.get(cid, [])
        if not nodes:
            return f"Community {cid} not found."
        header = _community_header(cid, G.nodes[nodes[0]].get("community_name"))
        lines = [f"{header} ({len(nodes)} nodes):"]
        for n in nodes:
            d = G.nodes[n]
            lines.append(
                f"  {sanitize_label(d.get('label', n))} "
                f"[{sanitize_label(str(d.get('source_file', '')))}]"
            )
        budget = int(arguments.get("token_budget", 2000))
        return _cut_lines_to_budget(
            lines, budget, "Raise token_budget or use get_node for specific members"
        )

    def _tool_god_nodes(arguments: dict) -> str:
        from graphify.analyze import god_nodes as _god_nodes

        nodes = _god_nodes(G, top_n=int(arguments.get("top_n", 10)))
        lines = ["God nodes (most connected):"]
        lines += [f"  {i}. {n['label']} - {n['degree']} edges" for i, n in enumerate(nodes, 1)]
        return "\n".join(lines)

    def _tool_graph_stats(_: dict) -> str:
        confs = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
        total = len(confs) or 1
        return (
            f"Nodes: {G.number_of_nodes()}\n"
            f"Edges: {G.number_of_edges()}\n"
            f"Communities: {len(communities)}\n"
            f"EXTRACTED: {round(confs.count('EXTRACTED') / total * 100)}%\n"
            f"INFERRED: {round(confs.count('INFERRED') / total * 100)}%\n"
            f"AMBIGUOUS: {round(confs.count('AMBIGUOUS') / total * 100)}%\n"
        )

    def _tool_shortest_path(arguments: dict) -> str:
        src_scored = _score_nodes(G, [t.lower() for t in arguments["source"].split()])
        tgt_scored = _score_nodes(G, [t.lower() for t in arguments["target"].split()])
        if not src_scored:
            return f"No node matching source '{arguments['source']}' found."
        if not tgt_scored:
            return f"No node matching target '{arguments['target']}' found."
        src_nid = _pick_scored_endpoint(G, src_scored, arguments["source"])
        tgt_nid = _pick_scored_endpoint(G, tgt_scored, arguments["target"])
        if src_nid == tgt_nid:
            return (
                f"'{arguments['source']}' and '{arguments['target']}' both resolved to "
                f"the same node '{src_nid}'. Use a more specific label or the exact node ID."
            )
        warnings: list[str] = []
        for name, scored, nid in (
            ("source", src_scored, src_nid),
            ("target", tgt_scored, tgt_nid),
        ):
            if len(scored) >= 2 and nid == scored[0][1]:
                top, runner = scored[0][0], scored[1][0]
                if top > 0 and (top - runner) / top < 0.10:
                    warnings.append(
                        f"warning: {name} match was ambiguous "
                        f"(top score {top:g}, runner-up {runner:g})"
                    )
        max_hops = int(arguments.get("max_hops", 8))
        weighted = bool(arguments.get("weighted", True))
        path_nodes, _stats = find_shortest_path(G, src_nid, tgt_nid, weighted=weighted)
        if path_nodes is None:
            return (
                f"No path found between "
                f"'{G.nodes[src_nid].get('label', src_nid)}' and "
                f"'{G.nodes[tgt_nid].get('label', tgt_nid)}'."
            )
        hops = len(path_nodes) - 1
        if hops > max_hops:
            return f"Path exceeds max_hops={max_hops} ({hops} hops found)."
        segments = []
        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            if G.has_edge(u, v):
                datas = edge_datas(G, u, v)
                forward = True
            else:
                datas = edge_datas(G, v, u)
                forward = False
            rels = sorted({d.get("relation") for d in datas if d.get("relation")})
            rel = "/".join(rels) if rels else "related"
            confs = sorted({d.get("confidence") for d in datas if d.get("confidence")})
            conf_str = f" [{'/'.join(confs)}]" if confs else ""
            if i == 0:
                segments.append(G.nodes[u].get("label", u))
            if forward:
                segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
            else:
                segments.append(f"<--{rel}{conf_str}-- {G.nodes[v].get('label', v)}")
        prefix = ("\n".join(warnings) + "\n") if warnings else ""
        return prefix + f"Shortest path ({hops} hops):\n  " + " ".join(segments)

    def _tool_list_prs(arguments: dict) -> str:
        from graphify.prs import fetch_prs, fetch_worktrees, format_prs_text, _detect_default_branch

        repo = arguments.get("repo") or None
        base = arguments.get("base") or _detect_default_branch(repo)
        try:
            prs = fetch_prs(repo=repo, base=base)
        except RuntimeError as e:
            return f"Error: {e}"
        worktrees = fetch_worktrees()
        for pr in prs:
            pr.worktree_path = worktrees.get(pr.branch)
        return format_prs_text(prs, base)

    def _tool_get_pr_impact(arguments: dict) -> str:
        from graphify.prs import fetch_pr_files, compute_pr_impact, _gh, _parse_ci

        number = int(arguments["pr_number"])
        repo = arguments.get("repo") or None
        view_args = [
            "pr",
            "view",
            str(number),
            "--json",
            "title,headRefName,baseRefName,author,isDraft,reviewDecision,statusCheckRollup,updatedAt",
        ]
        if repo:
            view_args += ["--repo", repo]
        pr_data = _gh(*view_args)
        if pr_data is None:
            return f"PR #{number} not found or gh not authenticated."
        files = fetch_pr_files(number, repo)
        if not files:
            return f"PR #{number}: no changed files found (may require gh auth)."
        comms, nodes = compute_pr_impact(files, G)
        ci = _parse_ci(pr_data.get("statusCheckRollup") or [])
        lines = [
            f"PR #{number}: {pr_data['title']}",
            f"CI: {ci}  Review: {pr_data.get('reviewDecision') or 'none'}",
            f"Base: {pr_data['baseRefName']}  Author: {(pr_data.get('author') or {}).get('login', '?')}",
            f"\nGraph impact: {nodes} nodes across {len(comms)} communities",
            f"Communities touched: {comms}",
            f"Files changed ({len(files)}):",
        ]
        lines += [f"  {f}" for f in files[:20]]
        if len(files) > 20:
            lines.append(f"  … and {len(files) - 20} more")
        return "\n".join(lines)

    def _tool_triage_prs(arguments: dict) -> str:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from graphify.prs import (
            fetch_prs,
            fetch_worktrees,
            fetch_pr_files,
            compute_pr_impact,
            _STATUS_ORDER,
            _detect_default_branch,
        )

        repo = arguments.get("repo") or None
        base = arguments.get("base") or _detect_default_branch(repo)
        try:
            prs = fetch_prs(repo=repo, base=base)
        except RuntimeError as e:
            return f"Error: {e}"
        worktrees = fetch_worktrees()
        for pr in prs:
            pr.worktree_path = worktrees.get(pr.branch)
        actionable = [
            p for p in prs if p.base_branch == base and p.status not in ("WRONG-BASE", "STALE")
        ]
        if not actionable:
            return f"No actionable PRs targeting {base}."
        workers = min(8, len(actionable))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_pr = {pool.submit(fetch_pr_files, pr.number, repo): pr for pr in actionable}
            for fut in as_completed(future_to_pr):
                pr = future_to_pr[fut]
                try:
                    files = fut.result()
                except Exception:
                    files = []
                if files:
                    pr.files_changed = files
                    pr.communities_touched, pr.nodes_affected = compute_pr_impact(files, G)
        header = (
            f"Actionable PRs targeting {base}: {len(actionable)}\n"
            "Rank these by review priority. Higher blast_radius = more graph communities affected = higher merge risk.\n"
        )
        lines = [header]
        for p in sorted(
            actionable,
            key=lambda x: _STATUS_ORDER.index(x.status) if x.status in _STATUS_ORDER else 99,
        ):
            impact = f"  blast_radius={p.blast_radius}" if p.blast_radius else ""
            wt = f"  worktree={p.worktree_path}" if p.worktree_path else ""
            lines.append(
                f"PR #{p.number} [{p.status}] CI={p.ci_status} review={p.review_decision or 'none'} "
                f"age={p.days_old}d author={p.author}{impact}{wt}\n  title: {p.title}"
            )
        return "\n\n".join(lines)

    def _tool_reason_with_graph(arguments: dict) -> str:
        from graphify.bidirectional_reasoner import reason

        result = reason(
            G,
            arguments["question"],
            revise=bool(arguments.get("revise", True)),
            community_aware=bool(arguments.get("community_aware", True)),
            weighted=bool(arguments.get("weighted", True)),
        )
        return result.final_answer

    def _tool_validate_proposal(arguments: dict) -> str:
        from graphify.preexec_validate import (
            proposal_from_tool_input,
            validate_code,
            validate_proposal,
        )

        strict = bool(arguments.get("strict", False))
        code = arguments.get("code")
        if code:
            report = validate_code(G, str(code), strict=strict)
        elif arguments.get("tool_input") is not None:
            action = proposal_from_tool_input(
                arguments.get("tool_name"),
                arguments.get("tool_input") or {},
            )
            report = validate_proposal(G, action, strict=strict)
        else:
            return "Provide either 'code' or 'tool_input' for validation."
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    _handlers = {
        "query_graph": _tool_query_graph,
        "get_node": _tool_get_node,
        "get_neighbors": _tool_get_neighbors,
        "get_community": _tool_get_community,
        "god_nodes": _tool_god_nodes,
        "graph_stats": _tool_graph_stats,
        "shortest_path": _tool_shortest_path,
        "list_prs": _tool_list_prs,
        "get_pr_impact": _tool_get_pr_impact,
        "triage_prs": _tool_triage_prs,
        "reason_with_graph": _tool_reason_with_graph,
        "validate_proposal": _tool_validate_proposal,
    }

    def _load_community_labels() -> dict[int, str]:
        labels_path = Path(active_graph_path).parent / ".graphify_labels.json"
        if labels_path.exists():
            try:
                return {
                    int(k): v
                    for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()
                }
            except Exception:
                pass
        return {cid: f"Community {cid}" for cid in communities}

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=AnyUrl("graphify://report"),
                name="Graph Report",
                description="Full GRAPH_REPORT.md",
                mimeType="text/markdown",
            ),
            types.Resource(
                uri=AnyUrl("graphify://stats"),
                name="Graph Stats",
                description="Node/edge/community counts and confidence breakdown",
                mimeType="text/plain",
            ),
            types.Resource(
                uri=AnyUrl("graphify://god-nodes"),
                name="God Nodes",
                description="Top 10 most-connected nodes",
                mimeType="text/plain",
            ),
            types.Resource(
                uri=AnyUrl("graphify://surprises"),
                name="Surprising Connections",
                description="Cross-community surprising connections",
                mimeType="text/plain",
            ),
            types.Resource(
                uri=AnyUrl("graphify://audit"),
                name="Confidence Audit",
                description="EXTRACTED/INFERRED/AMBIGUOUS edge breakdown",
                mimeType="text/plain",
            ),
            types.Resource(
                uri=AnyUrl("graphify://questions"),
                name="Suggested Questions",
                description="Suggested questions for this codebase",
                mimeType="text/plain",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        _select_graph(None)
        uri_str = str(uri)
        if uri_str == "graphify://report":
            report_path = Path(active_graph_path).parent / "GRAPH_REPORT.md"
            if report_path.exists():
                return report_path.read_text(encoding="utf-8")
            return "GRAPH_REPORT.md not found. Run graphify extract first."
        if uri_str == "graphify://stats":
            return _tool_graph_stats({})
        if uri_str == "graphify://god-nodes":
            return _tool_god_nodes({"top_n": 10})
        if uri_str == "graphify://surprises":
            try:
                from graphify.analyze import surprising_connections

                surprises = surprising_connections(G, communities, top_n=10)
                if not surprises:
                    return "No surprising connections found."
                lines = ["Surprising cross-community connections:"]
                for s in surprises:
                    lines.append(
                        f"  {s.get('source', '')} <-> {s.get('target', '')} [{s.get('relation', '')}]"
                    )
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not compute surprising connections: {exc}"
        if uri_str == "graphify://audit":
            confs = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
            total = len(confs) or 1
            return (
                f"Total edges: {total}\n"
                f"EXTRACTED: {confs.count('EXTRACTED')} ({round(confs.count('EXTRACTED') / total * 100)}%)\n"
                f"INFERRED: {confs.count('INFERRED')} ({round(confs.count('INFERRED') / total * 100)}%)\n"
                f"AMBIGUOUS: {confs.count('AMBIGUOUS')} ({round(confs.count('AMBIGUOUS') / total * 100)}%)\n"
            )
        if uri_str == "graphify://questions":
            try:
                from graphify.analyze import suggest_questions

                community_labels = _load_community_labels()
                questions = suggest_questions(G, communities, community_labels, top_n=10)
                if not questions:
                    return "No suggested questions available."
                lines = ["Suggested questions:"]
                for q in questions:
                    if isinstance(q, dict):
                        lines.append(f"  - {q.get('question', '')}")
                    else:
                        lines.append(f"  - {q}")
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not generate questions: {exc}"
        raise ValueError(f"Unknown resource: {uri_str}")

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        arguments = dict(arguments or {})
        project_path = arguments.pop("project_path", None)
        handler = _handlers.get(name)
        if not handler:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            _select_graph(project_path)
            return [types.TextContent(type="text", text=handler(arguments))]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error executing {name}: {exc}")]

    return server


def serve(graph_path: str | None = None) -> None:
    """Start the MCP server over stdio (the default, per-developer transport)."""
    graph_path = graph_path or _default_graph_json()
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as e:
        raise ImportError('mcp not installed. Run: pip install "graphifyy[mcp]"') from e
    import asyncio

    server = _build_server(graph_path)

    async def main() -> None:
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    _filter_blank_stdin()
    asyncio.run(main())


class _MCPASGIApp:
    """Raw-ASGI wrapper around the Streamable HTTP session manager.

    Passed to a Starlette ``Route`` as a class instance (not a function) so
    Starlette treats it as an ASGI app: it serves the exact mount path for all
    methods (GET/POST/DELETE) with no request/response wrapping and no
    trailing-slash redirect — mirroring how FastMCP mounts the same manager.
    """

    def __init__(self, manager) -> None:
        self._manager = manager

    async def __call__(self, scope, receive, send) -> None:
        await self._manager.handle_request(scope, receive, send)


class _ApiKeyMiddleware:
    """Pure-ASGI API-key gate for the HTTP transport.

    Implemented as raw ASGI (not Starlette's BaseHTTPMiddleware) on purpose:
    BaseHTTPMiddleware buffers responses and breaks the Streamable HTTP SSE
    stream. This short-circuits with 401 before the request ever reaches the
    session manager, leaving the streaming path untouched for authorized calls.
    """

    def __init__(self, app, api_key: str) -> None:
        self.app = app
        self._expected = api_key.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        import hmac

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"x-api-key")
        if provided is None:
            scheme, _, token = headers.get(b"authorization", b"").partition(b" ")
            if scheme.lower() == b"bearer" and token:
                provided = token.strip()
        if provided is None or not hmac.compare_digest(provided, self._expected):
            body = b'{"error": "unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _build_http_app(
    graph_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    api_key: str | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    session_timeout: float | None = 3600.0,
):
    """Build the Starlette ASGI app for the Streamable HTTP transport.

    Split out from :func:`serve_http` (which blocks on uvicorn) so the wiring
    can be exercised with an in-process ASGI test client.

    ``session_timeout`` reaps stateful sessions idle for that many seconds so a
    long-running shared server does not leak memory when IDE clients disconnect
    without sending a DELETE. ``None`` (or <= 0) disables reaping; it is forced
    to ``None`` in stateless mode, which has no sessions to reap.
    """
    try:
        import contextlib

        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route

        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as e:
        raise ImportError(
            "HTTP transport needs the mcp extra (mcp + starlette + uvicorn). "
            'Run: pip install "graphifyy[mcp]"'
        ) from e

    api_key = (api_key or "").strip() or None

    server = _build_server(graph_path)

    if host in ("0.0.0.0", "::", ""):
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        allowed = {host, "localhost", "127.0.0.1"}
        allowed |= {f"{h}:{port}" for h in list(allowed)}
        security = TransportSecuritySettings(allowed_hosts=sorted(allowed))

    idle_timeout = (
        None if (stateless or not session_timeout or session_timeout <= 0) else session_timeout
    )

    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
        security_settings=security,
        session_idle_timeout=idle_timeout,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    middleware = []
    if api_key:
        middleware.append(Middleware(_ApiKeyMiddleware, api_key=api_key))

    return Starlette(
        routes=[Route(path, endpoint=_MCPASGIApp(manager))],
        middleware=middleware,
        lifespan=lifespan,
    )


def serve_http(
    graph_path: str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    api_key: str | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    session_timeout: float | None = 3600.0,
) -> None:
    """Start the MCP server over Streamable HTTP (MCP spec 2025-03-26).

    Serves the same tools/resources as the stdio transport, so a single shared
    process can host the graph for a whole team. Clients point their IDE MCP
    config at ``http://<host>:<port><path>`` (default ``/mcp``).

    ``api_key`` (or the ``GRAPHIFY_API_KEY`` env var) enables a simple header
    check (``Authorization: Bearer <key>`` or ``X-API-Key: <key>``). OAuth is a
    deliberate follow-up. Binding ``0.0.0.0`` exposes the server beyond
    localhost — set an api_key when you do.
    """
    graph_path = graph_path or _default_graph_json()
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(
            "HTTP transport needs the mcp extra (mcp + starlette + uvicorn). "
            'Run: pip install "graphifyy[mcp]"'
        ) from e

    api_key = (api_key or "").strip() or None

    app = _build_http_app(
        graph_path,
        host=host,
        port=port,
        api_key=api_key,
        path=path,
        json_response=json_response,
        stateless=stateless,
        session_timeout=session_timeout,
    )

    auth_note = "api-key required" if api_key else "no auth (set --api-key to require one)"
    print(
        f"graphify MCP server (streamable-http) on http://{host}:{port}{path} - {auth_note}",
        file=sys.stderr,
    )
    if host in ("0.0.0.0", "::", "") and not api_key:
        print(
            f"WARNING: binding {host or '0.0.0.0'} with no api-key exposes the graph "
            "unauthenticated on the network. Set --api-key (or GRAPHIFY_API_KEY).",
            file=sys.stderr,
        )
    uvicorn.run(app, host=host, port=port)


def _main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="python -m graphify.serve",
        description="Serve a graphify knowledge graph over MCP (stdio or Streamable HTTP).",
    )
    parser.add_argument(
        "graph_path",
        nargs="?",
        default=None,
        help="Path to graph.json (default: graphify-out/graph.json)",
    )
    parser.add_argument(
        "--graph",
        dest="graph_flag",
        default=None,
        metavar="PATH",
        help="Path to graph.json — alias for the positional argument",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to serve on (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP bind port (default: 8080)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GRAPHIFY_API_KEY"),
        help="Require this key on the HTTP transport (env: GRAPHIFY_API_KEY)",
    )
    parser.add_argument("--path", default="/mcp", help="HTTP mount path (default: /mcp)")
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Return plain JSON responses instead of SSE streams",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Run without per-session state (for load-balanced / CI deployments)",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=3600.0,
        help="Reap stateful sessions idle this many seconds (default: 3600; 0 disables)",
    )
    args = parser.parse_args(argv)
    graph_path = args.graph_flag or args.graph_path or _default_graph_json()

    if args.transport == "http":
        serve_http(
            graph_path,
            host=args.host,
            port=args.port,
            api_key=args.api_key,
            path=args.path,
            json_response=args.json_response,
            stateless=args.stateless,
            session_timeout=args.session_timeout,
        )
    else:
        serve(graph_path)


if __name__ == "__main__":
    _main()
