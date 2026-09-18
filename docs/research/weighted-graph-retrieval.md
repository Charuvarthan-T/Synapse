# Relationship-Aware Weighted Graph Retrieval

Research contribution for Synapse (built on the Graphify knowledge-graph engine).

## Motivation

Code knowledge graphs encode heterogeneous relations — `calls`, `inherits`,
`implements`, `references`, `imports`, and many more. Legacy Graphify retrieval
treats every edge as a unit hop. That is correct for pure connectivity, but it
ignores semantic priority: a call edge is usually more informative for “how does
this execute?” than a transitive import edge.

Synapse introduces **relationship-aware weighting** so traversal and ranking
prefer stronger relations without abandoning hop-bounded, deterministic retrieval.

## Problem statement

Given a knowledge graph \(G=(V,E)\) with edge attributes `relation` and
`confidence`, retrieval should:

1. Expand neighborhoods in an order that prefers high-importance relations.
2. Choose shortest paths by **relation cost**, not hop count alone.
3. Rank returned edges/nodes so token-budget truncation keeps the most meaningful
   context.
4. Remain backward compatible with existing Graphify APIs (`weighted=False`
   restores legacy hop semantics).

## Design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Central registry | `RelationWeightRegistry` in `graphify/weights.py` | Single source of truth; no scattered magic numbers |
| Importance vs cost | Importance ↑ better; cost = 1/importance | Fits Dijkstra / NetworkX `weight=` |
| Confidence | EXTRACTED > INFERRED > AMBIGUOUS multipliers | Softens noisy semantic edges |
| Unknown relations | Configurable default importance (0.5) | Graceful on new AST/LLM edge types |
| Hop depth | Still enforced | Keeps query depth API stable |
| Default on | `weighted=True` for query/path | Synapse default; `--unweighted` for A/B |
| Scoring seeds | Unchanged lexical/IDF `_score_query` | Avoid breaking tightly locked score tests |

## Architecture

```
question / endpoints
        │
        ▼
┌───────────────────┐
│  seed resolution  │  (unchanged: _score_query / _pick_seeds)
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐     ┌──────────────────────────┐
│ weights.py        │────▶│ weighted_retrieval.py    │
│ RelationWeight    │     │ expand_neighborhood      │
│ Registry          │     │ find_shortest_path       │
└───────────────────┘     │ rank_nodes/edges         │
                          └────────────┬─────────────┘
                                       │
                                       ▼
                          serve._query_graph_text / CLI path / MCP
                                       │
                                       ▼
                          _subgraph_to_text (ranked context)
```

### Default importance (excerpt)

```
calls (1.0) > inherits (0.9) > implements (0.85)
  > references (0.6) > imports (0.4) > semantically_similar_to (0.2)
```

Full table: `DEFAULT_RELATION_IMPORTANCE` in `graphify/weights.py`.

### Extensibility

```python
from graphify.weights import RelationWeightRegistry

reg = RelationWeightRegistry().copy()
reg.register("annotates", 0.72)
# pass registry= into expand_neighborhood / find_shortest_path / _query_graph_text
```

Edge-level override: set `relation_weight` on an edge attribute dict.

## Implementation details

### Modules

- `graphify/weights.py` — registry, defaults, cost conversion helpers.
- `graphify/weighted_retrieval.py` — traversal, Dijkstra path, ranking, benchmark helpers.
- `graphify/serve.py` — `_bfs`/`_dfs`/`_query_graph_text`/`_subgraph_to_text` and MCP tools delegate here.
- `graphify/cli.py` — `--weighted` / `--unweighted` on `query` and `path`.

### Algorithms

1. **Weighted BFS (hop-limited):** same depth frontier as legacy BFS; within each
   hop, neighbors sorted by descending edge importance. Records
   `reach_importance` for ranking.
2. **Weighted DFS:** stack order prefers stronger relations.
3. **Path:** undirected cost view; `nx.shortest_path(..., weight="weight")`.
4. **Ranking:** seeds first; then `(distance, -reach_importance, -degree, id)`;
   edges sorted by importance before text render / budget cut.

### Failure modes

| Case | Behavior |
|------|----------|
| Unknown relation | `unknown_importance` |
| Missing edge metadata | same default |
| Disconnected endpoints | `None` path + empty stats |
| `weighted=False` | legacy hop BFS/DFS/path |

## Benchmark methodology

Script: `tests/bench_weighted_retrieval.py`

Compares unweighted vs weighted on a synthetic diamond (strong `calls` path vs
weak `imports` path) plus noise nodes:

- path node sequence and hop count
- geometric-mean path relation score
- BFS nodes/edges/explored counts
- median wall-clock ms over repeated expansions

Run:

```bash
uv run --frozen python tests/bench_weighted_retrieval.py
```

Results are environment-dependent; the script prints measured values only.

## CLI / MCP

```bash
graphify query "AuthService"              # weighted (default)
graphify query "AuthService" --unweighted
graphify path "A" "B"                     # weighted (default)
graphify path "A" "B" --unweighted
```

MCP tools `query_graph` and `shortest_path` accept boolean `weighted` (default true).

## Future extensions

- Learn relation weights from `reflect` / query-log feedback.
- Query-type conditioned weight profiles (e.g. “who calls X” boosts `calls`).
- Cost-bounded traversal (stop by cumulative cost, not only hops).
- Persist per-project weight overlays in `graphify-out/`.
- Integrate weights into `affected` impact analysis.

## Related tests

- `tests/test_weighted_retrieval.py` — unit/integration coverage
- Existing `tests/test_serve.py`, `tests/test_path_cli.py`, `tests/test_query_cli.py` — regression
