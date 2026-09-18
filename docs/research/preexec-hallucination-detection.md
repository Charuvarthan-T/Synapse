# Graph-Based Pre-Execution Hallucination Detection (Synapse C3)

## Problem

Agents often go:

```
LLM → generated code / tool call → execution → runtime failure
```

Hallucinated symbols, imports, and API relationships are discovered too late.

## Solution

Insert a graph validator **before** execution:

```
LLM → proposed Write/Edit/code
    → preexec_validate (VALID / INVALID / UNKNOWN)
    → execute  OR  nudge / deny (strict)
```

## Modules

| Module | Role |
|--------|------|
| `graphify/graph_checks.py` | Shared structural checks (reused with C2) |
| `graphify/preexec_validate.py` | Parse proposal → run checks → structured report |

## Integration points

1. **CLI** — `graphify preexec-check --code snippet.py [--strict]`
2. **PreToolUse** — `graphify hook-guard write --preexec`  
   Opt-in via `graphify claude install --preexec` or `GRAPHIFY_PREEXEC=1`.  
   Deny on INVALID only when `GRAPHIFY_PREEXEC_STRICT=1` / `--strict-preexec`.
3. **MCP** — tool `validate_proposal`
4. **Library** — `validate_proposal` / `validate_code`

Default behaviour is unchanged: pre-exec hooks are **off** unless opted in.

## Status model

| Status | Meaning | Blocks (strict)? |
|--------|---------|------------------|
| VALID | Graph supports the reference | No |
| INVALID | Graph contradicts (e.g. known class, missing method in inventory) | Yes |
| UNKNOWN | Graph has no evidence | **No** |

## Limitation

Synapse does not own a full in-process codegen→execute agent. The real
execution hosts are Claude Code / Copilot / MCP clients. C3 therefore plugs
into the earliest practical pre-execution gate available (Write/Edit
PreToolUse + MCP/CLI APIs) rather than inventing a disconnected demo runner.

Python AST extraction is first-class; other languages use heuristic parsers.
