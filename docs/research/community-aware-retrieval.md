# Hierarchical Community-Aware Code Retrieval

Research contribution for Synapse (built on the Graphify knowledge-graph engine).

## Motivation

Large code repositories produce knowledge graphs with thousands to millions of
nodes. Flat neighborhood expansion — even when relationship-weighted — still
searches a globally connected space. Architectural structure is already present
in the graph as **communities** (modules, subsystems, cohesive clusters), but
legacy retrieval treated community labels as display metadata only.

Synapse adds a **hierarchical community-aware retrieval** layer so queries first
identify the relevant repository community, then run existing weighted retrieval
inside that narrowed scope.

## Current limitation

Relationship-aware weighted retrieval improves *edge priority* within a hop
frontier. It does not answer:

- Which subsystem is this question about?
- How do we avoid flooding context with nodes from unrelated communities?

Without a community gate, high-degree bridges and weakly related imports can
still pull retrieval across architectural boundaries.

## Proposed architecture

```
Query
  │
  ▼
Seed resolution (_score_query / _pick_seeds)   ← unchanged
  │
  ▼
Community ranking (optional)
  │  high confidence → restrict to community subgraph
  │  low / failure   → fallback to full graph
  ▼
Weighted neighborhood expansion (existing)
  │
  ▼
Ranked context generation (_subgraph_to_text)
```

### Design goals

| Goal | Approach |
|------|----------|
| Modular detection | `CommunityDetector` ABC + factory |
| Reusable assignment | `CommunityIndex` / `CommunityRecord` |
| Compose with weights | Delegate to `expand_neighborhood` |
| API compatibility | `community_aware=False` by default |
| Safe degradation | Explicit fallback reasons |

## Implementation details

### Modules

| Module | Role |
|--------|------|
| `graphify/community_detection.py` | Pluggable detectors: `auto`, `leiden`, `louvain`, `label_propagation` |
| `graphify/community_index.py` | Lightweight metadata: id, members, representatives, size, name |
| `graphify/community_retrieval.py` | Ranking, scope restriction, `community_aware_expand` |
| `graphify/serve.py` | `_query_graph_text(..., community_aware=...)` |
| `graphify/cli.py` | `--community-aware` / `--no-community-aware` |

Build-time clustering in `graphify/cluster.py` is unchanged. When node
`community` attributes exist (from `to_json`), the index prefers them. Otherwise
query-time detection runs via the configured algorithm.

### Community selection

Communities are scored by:

1. Seed membership (dominant signal)
2. Lexical overlap with community name / representative / member labels

Confidence combines the top-vs-second score gap with seed coverage. If confidence
is below `community_min_confidence` (default `0.55`), or detection yields no
usable partition, retrieval falls back to the existing full-graph pipeline.

### Empty / small / disconnected graphs

| Case | Behavior |
|------|----------|
| Empty graph | Empty partition / empty index |
| No edges | One community per node |
| Fewer than 3 nodes | Component partition |
| Disconnected cliques | Detector separates components |
| Single community | Fallback (restriction is a no-op) |

## Retrieval flow

1. Resolve query terms and seeds (lexical/IDF — unchanged).
2. Optionally apply edge context filters.
3. If `community_aware`:
   - Build `CommunityIndex` (stored attrs or detector).
   - `select_communities` → selected ids or fallback.
   - Induce subgraph over selected members (+ seeds).
4. Run `expand_neighborhood` with existing `weighted` semantics.
5. Render ranked NODE/EDGE context under the token budget.

Header lines expose either `Communities=[...] (confidence=...)` or
`Community fallback (<reason>)` for transparency and A/B inspection.

## CLI / MCP

```bash
graphify query "auth login" --community-aware
graphify query "auth login" --community-aware --unweighted
graphify query "auth login"   # community_aware off (default)
```

MCP `query_graph` accepts boolean `community_aware` (default `false`).

## Benchmark methodology

Compare flat weighted retrieval vs community-aware weighted retrieval on a
two-module synthetic graph (dense intra-module `calls`, weak inter-module
`imports`):

1. Seed in module A with an A-oriented query.
2. Measure nodes visited / edges traversed / whether module B nodes appear.
3. Confirm fallback when seeds span multiple communities under a high
   confidence threshold.
4. Confirm `community_aware=False` matches baseline `expand_neighborhood`.

Unit coverage: `tests/test_community_retrieval.py`.

Regression: existing `tests/test_weighted_retrieval.py` remains the authority
for relation-weight behavior; community mode delegates to the same expansion
code.

## Future improvements

- Multi-resolution / true dendrogram hierarchy (parent communities).
- Persist community index beside `graph.json` for faster cold query start.
- Learn community priors from query-log feedback (`reflect`).
- Soft community penalties in cost graphs instead of hard restriction.
- Community-sectioned context rendering in `_subgraph_to_text`.

## Related tests

- `tests/test_community_retrieval.py` — detection, index, retrieval, fallbacks
- `tests/test_weighted_retrieval.py` — weighted retrieval regression
- `tests/test_cluster.py` — build-time Leiden/Louvain clustering
