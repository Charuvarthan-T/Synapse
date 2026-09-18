# Changelog

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
