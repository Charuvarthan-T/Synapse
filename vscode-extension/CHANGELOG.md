# Changelog

## 0.1.0

Initial v1 of **Synapse**: local knowledge-graph building (AST-only, no API
key, via the graphify engine), a branded Dashboard, sidebar tree view, graph
visualization webview, query/explain commands, and a GitHub Copilot Chat
Language Model Tool integration (`synapse_query`, `synapse_explain`).

Fixed: graph visualization now actually generates (`cluster-only` is
chained after `extract`/`update`, which only write `graph.json` on their
own).

Test suite: unit tests for graph data logic (`test/unit/`) and an
Electron-hosted integration test verifying activation and command/tool
registration against a real VS Code instance (`test/suite/`).
