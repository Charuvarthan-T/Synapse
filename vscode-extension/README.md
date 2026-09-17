# Synapse for VS Code (v1)

🧠 A local knowledge graph of your code, for your AI assistant. Synapse
builds a graph of your Python repository (functions, classes, calls,
imports, inheritance) entirely on your machine, and exposes it to GitHub
Copilot Chat so it can retrieve precise, relevant context instead of reading
whole files — using **your existing Copilot subscription**.

Synapse is a product built *on top of* the open-source
[`graphify`](https://github.com/Graphify-Labs/graphify) engine — it installs
graphify as its underlying dependency and drives it via its CLI. Synapse
does not fork or modify graphify itself.

## Why

Feeding an AI assistant whole files burns tokens and hits context/usage
limits fast. Synapse retrieves a small, targeted slice of your codebase
(the functions/classes actually relevant to the question, plus their direct
relationships) instead.

## What this extension does — and does not — do

- ✅ Builds a knowledge graph of your repo using local AST parsing
  (tree-sitter, via the graphify engine) — **no API key, no network calls,
  no code leaves your machine** for this core feature.
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
  with the `synapse.pythonPath` setting if it's not on your PATH)
- A Python workspace (the extension activates on Python files)

On first activation, the extension creates a small private virtual
environment at `~/.synapse/vscode-venv` and installs the `graphify` engine
into it — this happens once, automatically, with progress shown in the
status bar. (We use a short, fixed path here rather than VS Code's default
extension storage location specifically to avoid Windows' 260-character
path-length limit, which broke real extraction runs during this project's
own testing.)

## How to Use

1. **Open a Python project.** Synapse activates automatically when it sees
   `.py` files.
2. **Wait for the first build.** Watch the status bar in the bottom-right —
   it goes `provisioning...` → `building graph...` → `Synapse: N nodes`.
   This happens once automatically (toggle it off via
   `synapse.autoBuildOnOpen` if you'd rather trigger it manually).
   First-time setup needs Python 3.10+ on your PATH.
3. **The Dashboard opens automatically** the first time you install the
   extension — your graph's stats, every action, and a short "how it works"
   all in one branded screen. Reopen it anytime with **Synapse: Open
   Dashboard**, or click the status bar item.
4. **Browse the graph** via the Synapse icon (🧠) in the Activity Bar (God
   Nodes + Communities), or **Synapse: Open Graph Visualization** for the
   full interactive diagram.
5. **Ask it things** with **Synapse: Query Codebase**, or put your cursor
   on a symbol and run **Synapse: Explain Symbol at Cursor**.
6. **Let Copilot use it automatically** — once the graph exists, GitHub
   Copilot Chat's agent mode can call Synapse on its own when it needs
   codebase context. Nothing to configure.

| Command | What it does |
|---|---|
| Synapse: Open Dashboard | Your graph's stats + every action in one screen |
| Synapse: Rebuild Graph | Full rebuild (also click the status bar item) |
| Synapse: Update Graph (incremental) | Fast refresh after edits (runs automatically on save too) |
| Synapse: Query Codebase | Ask a question in plain language |
| Synapse: Explain Symbol at Cursor | Explain just the symbol under your cursor |
| Synapse: Open Graph Visualization | Interactive node/edge diagram |
| Synapse: Open Getting Started Guide | Reopen the step-by-step walkthrough |
| Synapse: Show Output Log | See what the underlying engine is doing (first stop if something looks wrong) |

If anything seems stuck or wrong, **Synapse: Show Output Log** is always
the right first step — every command Synapse runs against the graphify
engine, and its full output, is logged there.

## Features

- **A branded Dashboard**, not just a settings page — your graph's stats,
  every action, and a compact usage guide in one elegant screen. Open it
  with **Synapse: Open Dashboard**.
- **Auto-build on open**: builds the graph automatically when you open a
  Python workspace (toggle via `synapse.autoBuildOnOpen`).
- **Auto-update on save**: incrementally refreshes the graph a couple
  seconds after you save a Python file (toggle via
  `synapse.autoUpdateOnSave`). This is AST-only — free, no API key.
- **Sidebar view** (🧠 icon in the Activity Bar): browse God Nodes
  (the most-connected symbols — usually your core abstractions) and
  Communities (clusters of related code); click any symbol to jump to its
  definition.
- **Graph visualization**: `Synapse: Open Graph Visualization` opens an
  interactive, Synapse-branded node/edge view of your codebase.
- **Manual query/explain**: `Synapse: Query Codebase` and `Synapse:
  Explain Symbol at Cursor` commands for ad-hoc exploration, independent of
  any AI assistant.
- **Copilot Chat integration**: once the graph exists, Copilot Chat's agent
  mode can automatically call `synapseQuery` / `synapseExplain` when it
  needs codebase context — no setup required beyond having the graph built.

## Settings

| Setting | Default | Description |
|---|---|---|
| `synapse.autoBuildOnOpen` | `true` | Build the graph automatically on opening a Python workspace |
| `synapse.autoUpdateOnSave` | `true` | Incrementally refresh the graph after saving a Python file |
| `synapse.pythonPath` | `""` (auto-detect) | Explicit path to a Python 3.10+ interpreter |
| `synapse.queryBudget` | `2000` | Max tokens Synapse includes per query response |
| `synapse.devPackagePath` | `""` | Install the graphify engine from a local path instead of PyPI (extension development only) |

## Known v1 limitations

- Single-root workspaces only (first folder is used; multi-root support is
  a follow-up).
- Python only in this version (the graphify engine itself supports 20+
  languages — scoping v1 to Python keeps the extension small and testable).
- No BYOK semantic-extraction UI yet — the graph is always built
  `--code-only` (AST-based), which covers calls/imports/inheritance but not
  LLM-inferred conceptual relationships.
- No Claude Code / Gemini CLI / Antigravity integration yet (Copilot Chat
  only).
- Untrusted workspaces are not indexed (by design, for safety) — trust the
  workspace to use Synapse.

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
release, set `synapse.devPackagePath` to the path of the repo root
(one level up) before running `Synapse: Rebuild Graph`.

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
`synapse.autoBuildOnOpen`/`autoUpdateOnSave` disabled so the test stays
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

## Publishing / Distributing

Synapse isn't on the VS Code Marketplace yet. The simplest way to get it
into people's hands right now is a **GitHub Release with the packaged
`.vsix` attached**.

### 1. Package it

```bash
cd vscode-extension
npm install
npm run package
# produces synapse-0.1.0.vsix
```

(`npm run package` runs `vsce package --allow-missing-repository` — add a
`repository` field to `package.json` and a `LICENSE` file to drop that flag
and the accompanying warnings before a real public release.)

### 2. Create the GitHub Release

```bash
git tag vscode-extension-v0.1.0
git push origin vscode-extension-v0.1.0
gh release create vscode-extension-v0.1.0 \
  vscode-extension/synapse-0.1.0.vsix \
  --title "Synapse v0.1.0" \
  --notes "Initial release: local knowledge graph + Copilot Chat integration, no API key required."
```

(Or do the same thing through the GitHub web UI: **Releases → Draft a new
release → attach the `.vsix` file**.)

### 3. How users install it

No Marketplace account, no publishing pipeline needed on their end:

1. Download the `.vsix` from the release page.
2. In VS Code: Command Palette → **Extensions: Install from VSIX...** → pick
   the downloaded file. (Or from a terminal: `code --install-extension
   synapse-0.1.0.vsix`.)
3. Make sure Python 3.10+ is installed and on their PATH.
4. Open a Python project — Synapse takes it from there.

Since the underlying `graphify` engine isn't on PyPI yet either, users who
hit a `pip install graphifyy` failure during first-run provisioning should
either wait for it to be published, or set `synapse.devPackagePath` to a
local clone of the graphify repo as a workaround (see **Development**
above) — worth calling out clearly in the release notes.

### The best way to use it, in short

**GitHub Release + `.vsix` install** is the right distribution method for
now: it needs nothing from users beyond VS Code and Python, requires no
Marketplace review or publisher account, and you fully control the release
notes and version history via git tags. Move to publishing on the VS Code
Marketplace (and Open VSX, for VSCodium/other forks) once you want
one-click installs and automatic updates for a wider audience — that's a
`vsce publish` call away once you're ready, using the same `.vsix` you're
already building.
