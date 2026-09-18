# Synapse for VS Code

**Graph-grounded answers about your codebase.** Synapse builds a knowledge graph
of your code on your machine, answers questions with the AI you already use, and
checks every answer (and AI-written code) against that graph.

It runs the **Synapse engine from this repository**: our research fork of graphify
with relationship-weighted retrieval, community-aware retrieval, semantic code
graphs, bidirectional LLM↔graph reasoning and pre-execution hallucination checks.
The engine ships inside the extension; nothing is installed from the upstream
graphify package.

## What you can do

| | Feature | Uses AI? |
|---|---|---|
| **Ask** | Ask anything about the codebase. Synapse retrieves context from the graph, your AI drafts an answer with explicit claims, and every claim is **validated against the graph**. Contradicted claims are fed back and the answer is revised. You see which claims are supported, unverified or contradicted. | Yes, your own |
| **Search** | Find the symbols and relationships relevant to a question with relationship-weighted, community-aware graph traversal. | No |
| **Verify code** | Select code (for example something an AI just wrote) and check its imports, calls and methods against the graph. Flags APIs that don't exist in your repo. | No |
| **Explain symbol** | Right-click a symbol to see where it's defined, what it calls, what calls it, what it imports. | No |
| **Enrich with AI** | Add intent relations (`handles`, `validates`, `manages`…) between code units. Enriched relations are used by retrieval and survive later rebuilds. | Yes, your own |
| **Graph map** | Explore the whole codebase as an interactive graph. Double-click a node to open its source. | No |
| **Copilot agent tools** | In GitHub Copilot's agent mode, Copilot can call `#synapseSearch`, `#synapseExplain` and `#synapseVerify` on its own. | Copilot's |

Synapse indexes 25+ languages (Python, TypeScript/JavaScript, Java, Go, Rust,
C/C++, C#, Ruby, PHP, Swift, Kotlin and more). Code verification is most precise
for Python.

## No API key needed

Synapse never asks for an API key. **Ask** and **Enrich** use the assistant you're
already signed in to:

| Provider | How Synapse reaches it |
|---|---|
| **GitHub Copilot** | VS Code's Language Model API. VS Code asks you once to allow Synapse to use the model. |
| **Claude Code** | The `claude` CLI bundled with the Claude Code extension (or on your PATH), using your Claude sign-in. Runs with tools disabled. |
| **Codex** | The `codex` CLI bundled with the Codex extension (or on your PATH), using your ChatGPT sign-in. Runs read-only in an empty folder. |

**Auto** (the default) picks GitHub Copilot if you're signed in, then Claude Code,
then Codex. To choose a provider and model, click the model name under the question
box, or run **Synapse: Choose AI Model**.

Building the graph, searching, verifying, explaining and the graph map are
**100% local** and never use an AI.

## Install

Synapse is distributed through **GitHub Releases**.

1. Download `synapse-<version>.vsix` from the
   [latest release](https://github.com/Charuvarthan-T/Synapse/releases).
2. In VS Code, open the Command Palette and run **Extensions: Install from VSIX...**,
   then pick the file. Or run this in a terminal:

   ```bash
   code --install-extension synapse-0.2.0.vsix
   ```

3. Install **Python 3.10 or newer** if you don't have it ([python.org](https://www.python.org/downloads/)).

Installing a newer `.vsix` over an older one upgrades Synapse in place. If you had
Synapse 0.1.x, its engine is replaced with the Synapse engine automatically the
first time you use 0.2.0.

## Getting started

1. Open a folder with source code. Synapse builds the graph automatically (turn this
   off with `synapse.autoBuildOnOpen`). The first run sets up a private Python
   environment and takes about a minute; later builds take seconds.
2. Click the **Synapse** icon in the Activity Bar.
3. Type a question and press **Enter**. Switch between **Ask** (your AI, graph-checked)
   and **Search** (local) with the toggle.
4. Right-click in the editor for **Explain Symbol** and **Verify Code Against Graph**.

The graph refreshes incrementally a moment after you save a source file.

## Commands

| Command | |
|---|---|
| Synapse: Ask About This Codebase | Graph-grounded, graph-validated answer from your AI |
| Synapse: Search Graph | Local, weighted and community-aware retrieval |
| Synapse: Explain Symbol | Definition and direct relationships of the symbol at the cursor |
| Synapse: Verify Code Against Graph | Hallucination check of the selection (or whole file) |
| Synapse: Enrich Graph with AI Relations | Add AI-inferred intent relations to the graph |
| Synapse: Open Graph Map | Interactive map of the codebase |
| Synapse: Build Graph | Rebuild the graph |
| Synapse: Choose AI Model | Pick GitHub Copilot, Claude Code or Codex, and a model |
| Synapse: Show Log | Everything Synapse and its engine did (first stop when troubleshooting) |

## Settings

| Setting | Default | |
|---|---|---|
| `synapse.ai.provider` | `auto` | `auto`, `copilot`, `claude-code` or `codex` |
| `synapse.ai.model` | empty | Model for the provider; empty uses the provider's default |
| `synapse.autoBuildOnOpen` | `true` | Build when a workspace with source code opens |
| `synapse.autoUpdateOnSave` | `true` | Refresh the graph after saving a source file |
| `synapse.queryBudget` | `2000` | Max tokens of graph context per search (also for Copilot's tools) |
| `synapse.pythonPath` | empty | Python 3.10+ used to create the engine environment (auto-detected) |
| `synapse.devPackagePath` | empty | Development only: use a local checkout of the engine (editable install) |

## Privacy and security

- The graph lives in `graphify-out/` inside your workspace. Add it to `.gitignore`
  if you don't want to commit it.
- **Ask** sends your question and the retrieved graph context (symbol names, file
  paths, line numbers and relationships, not source code) to the AI you chose.
  **Enrich** sends symbol names and locations. Nothing else leaves your machine.
- Synapse talks to its engine over a loopback-only connection protected by a
  random per-run token, open only while a request runs.
- The engine runs in its own environment (`~/.synapse/vscode-venv`), isolated from
  your projects' Python environments. The first setup downloads the engine's open
  source dependencies (tree-sitter, networkx…) from PyPI.
- Synapse only runs in trusted workspaces. In Restricted Mode it stays idle, and
  workspace settings can't change the Python it runs.
- The graph map loads its graph library from the extension, not the internet.

## Troubleshooting

- **"Synapse needs Python 3.10 or newer"**: install Python, or point
  `synapse.pythonPath` at an interpreter, then click **Try again**.
- **"Installing the Synapse engine failed"**: the first setup needs internet access
  to PyPI. Behind a proxy, set `HTTPS_PROXY` before starting VS Code.
- **No AI available**: sign in to GitHub Copilot in VS Code, or install Claude Code or
  Codex and sign in once in a terminal. Then run **Synapse: Choose AI Model**.
- **Anything else**: run **Synapse: Show Log**. Every engine command and its output
  is recorded there.

## How it works

```
VS Code extension (TypeScript)
  ├─ sidebar + results panel (webviews)
  ├─ Copilot agent tools (vscode.lm.registerTool)
  └─ engine runner ──► ~/.synapse/vscode-venv/…/graphify   (the Synapse engine, bundled wheel)
                          extract · update · query --community-aware · explain
                          preexec-check · reason · semantic-graph
                          │
                          └─ LLM calls ──► editor-bridge (127.0.0.1, per-run token)
                                              └─► GitHub Copilot · Claude Code · Codex
```

`npm run build:engine` builds a wheel from this repository's `graphify/` package,
checks that it contains the Synapse modules, and records its SHA-256 in
`engine/manifest.json`. On first use, the extension creates the private venv and
installs that exact wheel. When a new extension version ships a different engine,
the venv is updated in place. The engine's `editor-bridge` LLM backend
(`graphify/llm.py`) sends each prompt back to the extension, which answers with
the chosen assistant.

## Development

Requirements: Node.js 20+, Python 3.10+ (and optionally [uv](https://docs.astral.sh/uv/)).

```bash
cd vscode-extension
npm install
npm run build:engine   # wheel of ../graphify -> engine/
npm run build:assets   # graph library + icons -> media/vendor/
npm run compile
```

Then press **F5** in VS Code to launch an Extension Development Host. To run against
your working copy of the engine without rebuilding the wheel, set
`synapse.devPackagePath` to the repository root.

### Tests

```bash
npm run test:unit         # pure logic: parsers, presenters, bridge, CLI providers
npm run test:engine       # real engine end to end (provisioning, build, search,
                          # verify, reasoning and enrichment through the bridge)
npm run test:integration  # the extension inside a real VS Code instance,
                          # including the Copilot path via a fake language model
npm test                  # all of the above
npm run package && npm run test:vsix
                          # installs the packaged .vsix into an isolated VS Code
                          # and smoke-tests it, including first-run engine setup
```

The engine and integration suites share a test engine environment in
`~/.synapse-e2e-test` (override with `SYNAPSE_TEST_HOME`). The integration suite
downloads a VS Code build into `.vscode-test/` on first run.

### Releasing

The [`VS Code extension`](../.github/workflows/vscode-extension.yml) workflow builds
and tests every change. To publish a release:

1. Bump `version` in `package.json` and add a `CHANGELOG.md` entry.
2. Tag and push:

   ```bash
   git tag vscode-v0.2.0
   ```

   ```bash
   git push origin vscode-v0.2.0
   ```

The workflow checks that the tag matches `package.json`, runs the test suites,
packages the `.vsix`, smoke-tests the installed package, and attaches it to a
GitHub Release. To build a
`.vsix` locally, run `npm run package`.
