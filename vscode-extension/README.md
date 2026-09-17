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

## How to Use

1. **Open a Python project.** Graphify activates automatically when it sees
   `.py` files.
2. **Wait for the first build.** Watch the status bar in the bottom-right —
   it goes `provisioning...` → `building graph...` → `Graphify: N nodes`.
   This happens once automatically (toggle it off via
   `graphify.autoBuildOnOpen` if you'd rather trigger it manually).
   First-time setup needs Python 3.10+ on your PATH.
3. **A "Get Started" guide opens automatically** the first time you install
   the extension, walking through the rest of these steps interactively.
   Reopen it anytime with **Graphify: Open Getting Started Guide** from the
   Command Palette.
4. **Browse the graph** via the Graphify icon in the Activity Bar (God Nodes
   + Communities), or **Graphify: Open Graph Visualization** for the full
   interactive diagram.
5. **Ask it things** with **Graphify: Query Codebase**, or put your cursor
   on a symbol and run **Graphify: Explain Symbol at Cursor**.
6. **Let Copilot use it automatically** — once the graph exists, GitHub
   Copilot Chat's agent mode can call Graphify on its own when it needs
   codebase context. Nothing to configure.

| Command | What it does |
|---|---|
| Graphify: Rebuild Graph | Full rebuild (also click the status bar item) |
| Graphify: Update Graph (incremental) | Fast refresh after edits (runs automatically on save too) |
| Graphify: Query Codebase | Ask a question in plain language |
| Graphify: Explain Symbol at Cursor | Explain just the symbol under your cursor |
| Graphify: Open Graph Visualization | Interactive node/edge diagram |
| Graphify: Open Getting Started Guide | Reopen the walkthrough |
| Graphify: Show Output Log | See what the underlying CLI is doing (first stop if something looks wrong) |

If anything seems stuck or wrong, **Graphify: Show Output Log** is always
the right first step — every command graphify runs, and its full
output, is logged there.

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

### Testing

```bash
npm test              # unit tests, then the Electron integration suite
npm run test:unit      # pure-logic tests (graphModel.ts), no VS Code needed
npm run test:integration  # downloads a real VS Code build once, then
                           # activates the extension in it and checks
                           # commands/tools are registered correctly
```

The integration test downloads a real VS Code binary into `.vscode-test/`
on first run (~1GB, cached afterwards) and runs against the fixture
workspace in `test/fixtures/sample-python-repo/`, which has
`graphify.autoBuildOnOpen`/`autoUpdateOnSave` disabled so the test stays
fast and doesn't depend on Python/pip being set up in CI. It checks
activation and command/tool registration only — it does not exercise a
real graph build (that path depends on the user's local Python
environment and is verified manually).

If you run this from inside an Electron-based terminal/tool (e.g. VS
Code's own integrated terminal, or Claude Code), note that
`ELECTRON_RUN_AS_NODE` is often already set in the environment and gets
inherited by the spawned test instance, breaking it in a confusing way
(it tries to `require()` the workspace path as a script). `test/runTest.ts`
already unsets it for the child process, but it's worth knowing about if
you see `Cannot find module <path>` errors from an Electron process here.
