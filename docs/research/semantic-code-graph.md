# Semantic Code Graph Enhancement

Research contribution for Synapse (built on the Graphify knowledge-graph engine).

## Motivation

Structural extraction captures *what calls what* — `calls`, `imports`,
`inherits`, `references`. It cannot capture *why*. Two functions connected by
`calls` might be doing completely different kinds of work:

```
Function A --calls--> Function B
```

says nothing about intent. The desired signal is closer to:

```
Function A --"handles authentication workflow"--> Function B
```

Retrieval, ranking, and community detection all benefit from this extra
layer: a query about "how is a session validated" should surface the
`validates` relationship even when the structural path between the two
functions is a weak, multi-hop `imports` chain.

## Current limitation

Before this contribution, the only source of relation types was AST
extraction (structural) plus LLM extraction restricted to *documents, papers,
and images* — code was intentionally excluded from semantic extraction at
the CLI/skill orchestration layer, because AST already covers code
structurally. That left a gap: no layer expresses code *intent* between two
code entities.

## Architecture

The enhancement adds three focused modules on top of the existing LLM stack
(`graphify/llm.py`) and graph builder (`graphify/build.py`) — it does not
introduce a second graph or a parallel extraction pipeline.

```
graph.json (existing)
        │
        ▼
select_code_units(G)              # graphify/semantic_extraction.py
        │
        ▼
batch_code_units(...)
        │
        ▼
SemanticLLMProvider.complete(...)  # provider abstraction over graphify.llm
        │
        ▼
parse_semantic_relations(...)     # graphify/semantic_relations.py
        │  (validated SemanticRelation objects; raw LLM text discarded)
        ▼
apply_semantic_relations(G, ...)  # graphify/semantic_graph.py
        │  (preserves structural edges; adds/augments)
        ▼
graph.json (extended, same schema)
```

### Modules

| Module | Responsibility |
|--------|-----------------|
| `graphify/semantic_relations.py` | Relation vocabulary, `SemanticRelation`/`SemanticExtractionResult` dataclasses, response parsing + validation |
| `graphify/semantic_extraction.py` | Provider abstraction, code-unit selection/batching, prompt building, retry policy, orchestration |
| `graphify/semantic_graph.py` | Safe graph integration — augments existing edges, adds new ones, creates concept nodes |
| `graphify/weights.py` | Registers default importances for the new relation types; `edge_importance` now also considers secondary relations |
| `graphify/cli.py` | `graphify semantic-graph` — run extraction over an existing `graph.json` and write it back |

### Design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Relation vocabulary | `handles`, `validates`, `manages`, `responsible_for`, `related_to` | Fixed, closed set — keeps ranking and prompts deterministic |
| LLM provider | `SemanticLLMProvider` protocol + `BackendLLMProvider` | Delegates all network/SDK concerns to `graphify.llm`; no duplicated backend code |
| Retry | `RetryingProvider` decorator, bounded attempts + backoff | Composable, testable with fakes, independent of any one backend's SDK retry |
| Storage format | `SemanticRelation` dataclass → typed edge attrs | Raw LLM text is never persisted; only validated, typed relations reach the graph |
| Edge collision | Secondary-relation list on existing edges | A simple `nx.Graph`/`DiGraph` edge is keyed only by `(u, v)`; blindly calling `add_edge` again would overwrite the structural `relation`. Augmenting instead preserves it |
| New pairs | Real graph edge with `relation=<semantic type>` | Lets weighted/community retrieval traverse relationships AST never saw |
| Unresolved endpoints | Lightweight `concept` node (already a valid `file_type`) | No dangling edges; endpoints like `"user_session"` still become part of the graph |
| Failure handling | Never raises past `extract_semantic_relations` | No API key / no code units / provider failure all degrade to an empty, explained result |

## Extraction flow

1. `select_code_units(G)` collects nodes with `file_type == "code"` in
   deterministic order (sorted by `source_file`, then `node_id`).
2. `batch_code_units(...)` splits them into fixed-size batches (default 20).
3. `build_semantic_prompt(batch)` renders a single deterministic prompt
   listing each unit's id, label, and source location, and the fixed
   relation vocabulary.
4. `SemanticLLMProvider.complete(...)` sends the prompt through the existing
   `graphify.llm._call_llm` backend dispatch (same API keys, same model
   overrides, same backends: gemini, openai, claude, ollama, ...).
5. `parse_semantic_relations(...)` parses the response with
   `graphify.llm._parse_llm_json` (fence-stripping, brace-matching, byte cap
   — all reused, not reimplemented), then validates every entry:
   - both endpoints present and non-empty,
   - relation type in the fixed vocabulary,
   - confidence normalized to `EXTRACTED` / `INFERRED` / `AMBIGUOUS`
     (defaulting to `INFERRED` — these are reasoned, not directly observed),
   - self-loops rejected,
   - unmatched endpoints become `concept` nodes instead of being dropped.
6. Failed batches are recorded as errors and skipped; extraction always
   returns a `SemanticExtractionResult`, never raises.

## Graph changes

`apply_semantic_relations(G, result)`:

- Adds any new `concept` nodes referenced by relations.
- For a `(source, target)` pair with **no existing edge**: adds a real graph
  edge with `relation`, `confidence`, `source_file`, and `_origin =
  "semantic_code"`.
- For a pair that **already has a structural edge** (e.g. `calls`): appends
  the semantic relation to a `secondary_relations` list on that edge instead
  of overwriting `relation`. Repeated runs are idempotent — the same
  relation type is not appended twice.
- Dangling relations (an endpoint absent from the graph and not resolvable
  to a concept node) are counted as skipped, never inserted.

This means:

- **Structural edges are preserved exactly.** `calls`, `imports`, `inherits`
  keep their original `relation` and `confidence`.
- **Edge metadata is supported.** `secondary_relations` is additive graph
  metadata, not a schema break.
- **Relationship-type filtering works unchanged** — `context_filters` in the
  existing query pipeline still filters on the edge's primary `relation`;
  semantic-only edges are filterable the same way once they carry
  `relation="handles"` (etc.) as their primary type.
- **Weighted retrieval works unchanged, and sees more.** `handles` (0.65),
  `manages` (0.65), `responsible_for` (0.6), `validates` (0.55), and
  `related_to` (0.3) are registered in `DEFAULT_RELATION_IMPORTANCE`
  (`graphify/weights.py`) — placed below structural relations but above weak
  similarity edges. `RelationWeightRegistry.edge_importance` additionally
  takes the max importance across `secondary_relations`, so an edge whose
  primary relation is a weak `imports` but carries a `handles` annotation
  still ranks appropriately.
- **Community retrieval works unchanged.** Community detection and indexing
  (`graphify/community_detection.py`, `graphify/community_index.py`) operate
  on graph topology regardless of relation type — new semantic edges simply
  become part of the connectivity they cluster over.

## CLI

```bash
graphify semantic-graph --graph graphify-out/graph.json
graphify semantic-graph --backend gemini --model gemini-3-flash-preview
graphify semantic-graph --batch-size 25 --limit 200 --dry-run
```

Loads an existing `graph.json`, runs semantic extraction with the detected
(or specified) backend, applies the result in place, and writes the graph
back — the same node-link JSON schema, now with additional edges/nodes.
`--dry-run` reports what would change without writing.

## Benchmark methodology

1. Build a synthetic code graph with a structural `calls` chain and at least
   one pair of nodes with no structural path.
2. Run extraction with a fake `SemanticLLMProvider` returning fixed relations
   (no network dependency for CI).
3. Compare weighted `expand_neighborhood` reachability before/after applying
   semantic relations — confirm previously unreachable nodes (connected only
   through a semantic edge) are now reachable.
4. Confirm structural edge `relation`/`confidence` values are byte-identical
   before and after semantic application (preservation check).
5. Confirm idempotency: running `apply_semantic_relations` twice with the
   same result produces the same edge count and no duplicate secondary
   relations.

Unit coverage: `tests/test_semantic_graph.py` (parsing, invalid responses,
provider retry/fallback, graph insertion, weighted/community retrieval
compatibility).

## Future improvements

- Read a short source snippet around `source_location` to give the LLM
  actual code text instead of label-only context.
- Cache semantic extraction results per `source_file` hash (mirroring
  `graphify.cache` for document extraction) to avoid re-querying unchanged
  code.
- Learn/adjust semantic relation importances from query-log feedback
  (`graphify.reflect`), matching the weighted-retrieval future-work item.
- Extend `_subgraph_to_text` to render `secondary_relations` explicitly in
  context output.
- Promote `--multigraph` (already probed in `graphify/multigraph_compat.py`)
  so structural and semantic edges between the same pair can coexist as
  true parallel edges instead of a secondary-relation list.

## Related tests

- `tests/test_semantic_graph.py` — parsing, validation, provider retry,
  graph insertion, weighted/community retrieval compatibility
- `tests/test_weighted_retrieval.py` — weighted retrieval regression
- `tests/test_community_retrieval.py` — community retrieval regression
- `tests/test_llm_backends.py`, `tests/test_llm_parser.py` — reused LLM
  backend/parsing infrastructure, unmodified
