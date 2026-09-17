# Changelog

## 0.1.0

Initial v1: local knowledge-graph building (AST-only, no API key), sidebar
tree view, graph visualization webview, query/explain commands, and a
GitHub Copilot Chat Language Model Tool integration (`graphify_query`,
`graphify_explain`).

Test suite: unit tests for graph data logic (`test/unit/`) and an
Electron-hosted integration test verifying activation and command/tool
registration against a real VS Code instance (`test/suite/`).
