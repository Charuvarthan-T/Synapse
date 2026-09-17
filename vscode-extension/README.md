# Graphify for VS Code (v1)

Local knowledge-graph context for AI coding assistants. Graphify builds a
graph of your Python repository (functions, classes, calls, imports,
inheritance) entirely on your machine, and exposes it to GitHub Copilot Chat
so it can retrieve precise, relevant context instead of reading whole files
— using **your existing Copilot subscription**.

## Why

Feeding an AI assistant whole files burns tokens and hits context/usage
limits fast. Graphify retrieves a small, targeted slice of your codebase
(the functions/classes actually relevant to the question, plus their direct
relationships) instead.

## What this extension does — and does not — do

- ✅ Builds a knowledge graph of your repo using local AST parsing
  (tree-sitter) — **no API key, no network calls, no code leaves your
  machine** for this core feature.
- ✅ Registers as a tool GitHub Copilot Chat's agent mode can call
  (`vscode.lm.registerTool`) — Copilot decides when to use it, using your
  own Copilot access. This extension never calls an LLM itself.
- ❌ Does not require or ever ask for an OpenAI/Anthropic/Gemini API key in
  v1. (Richer *semantic* extraction, which does use an LLM, is a
  documented future option — see [Roadmap](#roadmap) — and would always be
  bring-your-own-key, stored only in VS Code's secret storage.)
- ❌ v1 does not wire into Claude Code or Gemini CLI directly (that's MCP
  server integration, planned for v2 — see Roadmap). Copilot Chat is the
  only assistant integration in this version.

## Requirements

- VS Code 1.95+ (needed for the stable Language Model Tools API)
- Python 3.10+ available on your system (checked automatically; override
  with the `graphify.pythonPath` setting if it's not on your PATH)
- A Python workspace (the extension activates on Python files)

On first activation, the extension creates a small private virtual
environment at `~/.graphify/vscode-venv` and installs the `graphify`
package into it — this happens once, automatically, with progress shown in
the status bar. (We use a short, fixed path here rather than VS Code's
default extension storage location specifically to avoid Windows' 260-
character path-length limit, which broke real extraction runs during this
project's own testing.)

## Features

- **Auto-build on open**: builds the graph automatically when you open a
  Python workspace (toggle via `graphify.autoBuildOnOpen`).
- **Auto-update on save**: incrementally refreshes the graph a couple
  seconds after you save a Python file (toggle via
  `graphify.autoUpdateOnSave`). This is AST-only — free, no API key.
- **Sidebar view** (Graphify icon in the Activity Bar): browse God Nodes
  (the most-connected symbols — usually your core abstractions) and
  Communities (clusters of related code); click any symbol to jump to its
  definition.
- **Graph visualization**: `Graphify: Open Graph Visualization` opens an
  interactive node/edge view of your codebase.
- **Manual query/explain**: `Graphify: Query Codebase` and `Graphify:
  Explain Symbol at Cursor` commands for ad-hoc exploration, independent of
  any AI assistant.
- **Copilot Chat integration**: once the graph exists, Copilot Chat's agent
  mode can automatically call `graphifyQuery` / `graphifyExplain` when it
  needs codebase context — no setup required beyond having the graph built.

## Settings

| Setting | Default | Description |
|---|---|---|
| `graphify.autoBuildOnOpen` | `true` | Build the graph automatically on opening a Python workspace |
| `graphify.autoUpdateOnSave` | `true` | Incrementally refresh the graph after saving a Python file |
| `graphify.pythonPath` | `""` (auto-detect) | Explicit path to a Python 3.10+ interpreter |
| `graphify.queryBudget` | `2000` | Max tokens graphify includes per query response |
| `graphify.devPackagePath` | `""` | Install graphify from a local path instead of PyPI (extension development only) |

## Known v1 limitations

- Single-root workspaces only (first folder is used; multi-root support is
  a follow-up).
- Python only in this version (graphify itself supports 20+ languages —
  scoping v1 to Python keeps the extension small and testable).
- No BYOK semantic-extraction UI yet — the graph is always built
  `--code-only` (AST-based), which covers calls/imports/inheritance but not
  LLM-inferred conceptual relationships.
- No Claude Code / Gemini CLI / Antigravity integration yet (Copilot Chat
  only).
- Untrusted workspaces are not indexed (by design, for safety) — trust the
  workspace to use Graphify.

## Roadmap (not in v1)

- MCP server integration (`graphify-mcp`) so Claude Code, Gemini CLI, and
  Cursor can attach as an external tool using their own credentials.
- Optional BYOK semantic extraction (docs/papers/images + LLM community
  labeling), key stored in VS Code `SecretStorage`.
- Multi-root workspace support.
- Bundled Python runtime option for a fully zero-dependency install.

## Development

```bash
cd vscode-extension
npm install
npm run compile
# then press F5 in VS Code to launch an Extension Development Host
```

To develop against this repo's own `graphify` source instead of a PyPI
release, set `graphify.devPackagePath` to the path of the repo root
(one level up) before running `Graphify: Rebuild Graph`.
