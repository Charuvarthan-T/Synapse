# Changelog

## 0.4.0 (V4)

### New
- **Named communities.** Communities are no longer called "Community 0", "Community 1"…
  Every Build names each one after what it contains: the file (or folder) that holds
  most of its members and its most connected symbol, e.g. `app/auth.py · login`. No AI
  is used, and names stay stable across builds and incremental updates.
- **Synapse: Name Communities with AI** (also in the sidebar's Tools) gives communities
  readable titles such as "Authentication and Sessions" using your AI. Only symbol names
  and file paths are sent. Titles are kept until a community's members change; changed
  communities fall back to their content-based name. **Enrich Graph** now names
  communities as its last step.
- The sidebar lists the largest communities by name (click to open the most connected
  symbol); search results, Explain and the graph map legend show the names.
- Community names now help community-aware retrieval: they are matched against the
  question, which "Community N" never could be.

### Engine (in `graphify/`)
- `label_communities_by_content` in `graphify/cluster.py`; `graphify cluster-only
  --structural-labels`. `graphify update` drops a saved name when its community's
  membership changed since naming, instead of keeping a stale name.

## 0.3.1 (V3)

### Fixed
- The engine failed to start on Windows with Smart App Control on ("An Application
  Control policy has blocked this file"). Synapse now runs the engine through the
  signed Python interpreter in its private environment (`python -I -m graphify`)
  instead of the unsigned `graphify.exe` launcher that pip generates. Isolated mode
  keeps a `graphify/` folder in your workspace from shadowing the engine.

## 0.3.0 (V3)

### New
- **Ask reads your code, not just the graph.** Along with the graph context,
  Synapse now sends the source of the top-ranked symbols to your AI, the way AI
  code editors do, so answers can explain *how* code works and not only how it
  is connected. Excerpts follow the same weighted, community-aware ranking as the
  graph context, use exact function and class bodies for Python (nearest-symbol
  ranges for other languages), and stay within a token budget.
- The answer view lists **Code the AI read** with clickable `file:start-end`
  ranges, and the footer reports how much graph and code context was used.
- New settings: `synapse.ai.includeCode` (default on) and `synapse.ai.codeBudget`
  (default 3000 tokens). Enrich still never sends source code.
- Claims are still validated against the graph, and contradicted drafts are revised.

### Engine (in `graphify/`)
- `graphify reason --with-code [--code-budget TOKENS] [--root PATH]` adds source
  excerpts (`graphify/code_context.py`). Files are read only inside the project
  root, and excerpts are wrapped as untrusted data with injection sentinels
  neutralised. Without the flag the prompt is unchanged.
- Fixed: AI relations added by Enrich (stored as parallel multigraph edges) were
  lost, and could overwrite the structural edge on the same pair, when the graph
  was rebuilt with Build Graph.

## 0.2.0

Synapse now runs **the Synapse engine from this repository** instead of the
upstream `graphifyy` package from PyPI. 0.1.x installs are migrated automatically.

### New
- **Ask**: bidirectional LLM↔graph reasoning. Answers come from your own AI,
  grounded in graph context, with every claim validated against the graph and
  contradicted drafts revised. Claim verdicts are shown with links to the code.
- **Search** uses relationship-weighted, community-aware retrieval.
- **Verify Code Against Graph**: pre-execution hallucination check for selected
  code, also available to Copilot's agent mode as `#synapseVerify`.
- **Enrich Graph with AI Relations**: semantic intent relations layered onto the
  code graph.
- **No API key**: Ask and Enrich use GitHub Copilot (VS Code Language Model API),
  Claude Code or Codex through your existing sign-in. Pick a provider and model with
  **Synapse: Choose AI Model**.
- All languages the engine supports are indexed, not just Python.
- Redesigned UI: a single native-looking sidebar (question box, graph stats, key
  symbols, tools) and a results panel for answers, searches, explanations and
  verification reports. Follows your light, dark or high-contrast theme.
- The graph map works offline and opens source on double-click.
- Cancellable long-running operations, clear first-run and error states.

### Engine fixes (in `graphify/`)
- New `editor-bridge` LLM backend, so an editor can supply the model.
- Semantic relations are no longer dropped by `graphify update` or incremental
  `graphify extract`.

### Removed
- The separate Knowledge Graph tree view, docs panel and six-step walkthrough;
  their useful parts are now in the sidebar.

### Security
- The engine runs from its console script, so a `graphify/` folder in your
  workspace can no longer shadow it.
- Webviews use a strict Content Security Policy with no network access.
- `synapse.pythonPath` and `synapse.devPackagePath` can't be set by an untrusted
  workspace.

## 0.1.0

Initial release: local knowledge graph (AST only), dashboard, tree view, graph
visualization, query/explain commands and GitHub Copilot tools. Built on the
upstream graphify package.
