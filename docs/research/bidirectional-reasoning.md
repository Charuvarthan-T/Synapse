# Bidirectional LLM + Graph Reasoning (Synapse C2)

## Problem

Ordinary GraphRAG is one-way:

```
Graph → retrieved context → LLM → answer
```

There is no programmatic check that the LLM's structural assertions still
match the repository graph. Prompting "do not hallucinate" is not enough.

## Solution

Synapse adds an explicit **LLM → graph validation** loop on top of existing
retrieval (weighted + community-aware):

```
Graph facts
    → LLM draft answer + structured claims
    → graph_checks (SUPPORTED / CONTRADICTED / UNKNOWN)
    → optional revise prompt with validation evidence
    → final answer + evidence appendix
```

## Modules

| Module | Role |
|--------|------|
| `graphify/graph_checks.py` | Shared lookups: exists, calls, imports, inherits, method_of |
| `graphify/claims.py` | Parse/validate typed claims from LLM JSON |
| `graphify/bidirectional_reasoner.py` | Orchestrate retrieve → reason → validate → revise |

## CLI / MCP

```bash
graphify reason "How does login use the session store?" [--json] [--no-revise]
graphify validate-claims --claims claims.json
```

MCP tool: `reason_with_graph`.

## Verdict rules

- **SUPPORTED** — graph contains matching node/edge evidence.
- **UNKNOWN** — graph lacks evidence (never treated as proof of falsehood).
- **CONTRADICTED** — graph shows conflicting exclusive structure (e.g. inherits a different parent).

## What this is not

- Not graph-construction evidence binding (`llm._bind_node_evidence`).
- Not ordinary `graphify query` context stuffing.
- Not a second knowledge graph.
