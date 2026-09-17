import * as vscode from "vscode";
import * as cliService from "./cliService";
import { loadGraph, degreeByNode, groupByCommunity } from "./graphModel";

let currentPanel: vscode.WebviewPanel | undefined;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function statsHtml(workspaceRoot: string | undefined): string {
  if (!workspaceRoot || !cliService.hasGraph(workspaceRoot)) {
    return `
      <div class="stat-empty">
        No graph built yet for this workspace.
        <a class="btn primary" href="command:synapse.rebuildGraph">Build Graph</a>
      </div>`;
  }
  const graph = loadGraph(cliService.graphJsonPath(workspaceRoot));
  if (!graph) {
    return `<div class="stat-empty">Graph exists but couldn't be read. Try rebuilding.</div>`;
  }
  const communities = groupByCommunity(graph).size;
  const degree = degreeByNode(graph);
  const topNode = [...degree.entries()].sort((a, b) => b[1] - a[1])[0];
  const topLabel = topNode
    ? escapeHtml(graph.nodes.find((n) => n.id === topNode[0])?.label ?? topNode[0])
    : "—";

  return `
    <div class="stats">
      <div class="stat"><div class="stat-value">${graph.nodes.length}</div><div class="stat-label">nodes</div></div>
      <div class="stat"><div class="stat-value">${graph.edges.length}</div><div class="stat-label">edges</div></div>
      <div class="stat"><div class="stat-value">${communities}</div><div class="stat-label">communities</div></div>
      <div class="stat"><div class="stat-value" title="${topLabel}">${topLabel}</div><div class="stat-label">top node</div></div>
    </div>`;
}

function html(workspaceRoot: string | undefined): string {
  const csp = ["default-src 'none'", "style-src 'unsafe-inline'", "img-src data:"].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>Synapse</title>
<style>
  :root {
    --accent-1: #7c3aed;
    --accent-2: #22d3ee;
    --bg: #0f0f1a;
    --panel: #171727;
    --border: #2a2a4e;
    --text: #e6e6f0;
    --muted: #9a9ac0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .hero {
    display: flex; align-items: center; gap: 16px; margin-bottom: 8px;
  }
  .hero .logo { font-size: 40px; }
  .hero h1 {
    margin: 0; font-size: 28px; font-weight: 700;
    background: linear-gradient(90deg, var(--accent-1), var(--accent-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .tagline { color: var(--muted); margin: 0 0 28px 0; font-size: 14px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px; margin-bottom: 20px;
  }
  .card h2 { margin: 0 0 14px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; }
  .stat { flex: 1; min-width: 100px; background: rgba(124,58,237,0.08); border: 1px solid var(--border); border-radius: 10px; padding: 14px; text-align: center; }
  .stat-value { font-size: 20px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat-label { font-size: 11px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-empty { color: var(--muted); display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; }
  .btn {
    display: inline-block; padding: 9px 16px; border-radius: 8px; text-decoration: none;
    font-size: 13px; font-weight: 600; border: 1px solid var(--border); color: var(--text);
    background: rgba(255,255,255,0.03);
  }
  .btn:hover { background: rgba(255,255,255,0.08); }
  .btn.primary {
    background: linear-gradient(90deg, var(--accent-1), var(--accent-2)); color: #0f0f1a; border: none;
  }
  ol.steps { margin: 0; padding-left: 20px; color: var(--text); line-height: 1.9; font-size: 13px; }
  ol.steps b { color: var(--accent-2); }
  .footnote { color: var(--muted); font-size: 12px; margin-top: 24px; }
</style>
</head>
<body>
  <div class="hero">
    <span class="logo">\u{1F9E0}</span>
    <h1>Synapse</h1>
  </div>
  <p class="tagline">A local knowledge graph of your code, for your AI assistant. No API key. Nothing leaves your machine.</p>

  <div class="card">
    <h2>Your Graph</h2>
    ${statsHtml(workspaceRoot)}
  </div>

  <div class="card">
    <h2>Quick Actions</h2>
    <div class="actions">
      <a class="btn primary" href="command:synapse.rebuildGraph">Rebuild Graph</a>
      <a class="btn" href="command:synapse.updateGraph">Update Graph</a>
      <a class="btn" href="command:synapse.openGraphView">Open Visualization</a>
      <a class="btn" href="command:synapse.query">Query Codebase</a>
      <a class="btn" href="command:synapse.explainAtCursor">Explain at Cursor</a>
      <a class="btn" href="command:workbench.view.extension.synapse">Open Sidebar</a>
      <a class="btn" href="command:synapse.showOutput">Show Output Log</a>
    </div>
  </div>

  <div class="card">
    <h2>How It Works</h2>
    <ol class="steps">
      <li><b>Build</b> — Synapse parses your Python files locally (no LLM, no network) into a graph of functions, classes, calls, and imports.</li>
      <li><b>Explore</b> — browse God Nodes (your core abstractions) and Communities (related code clusters) in the sidebar, or see the whole thing in the visualization.</li>
      <li><b>Ask</b> — query the graph in plain language, or explain the symbol under your cursor.</li>
      <li><b>Let Copilot use it</b> — once built, GitHub Copilot Chat's agent mode can call Synapse on its own, using your existing subscription, to answer codebase questions with far fewer tokens than reading whole files.</li>
    </ol>
  </div>

  <p class="footnote">Synapse is built on top of the open-source <b>graphify</b> engine. Your code and API keys are never sent anywhere by Synapse itself.</p>
</body>
</html>`;
}

export function openDashboard(workspaceRoot: string | undefined): void {
  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Active);
  } else {
    currentPanel = vscode.window.createWebviewPanel(
      "synapseDashboard",
      "Synapse",
      vscode.ViewColumn.Active,
      { enableScripts: false, retainContextWhenHidden: true }
    );
    currentPanel.onDidDispose(() => (currentPanel = undefined));
  }
  currentPanel.webview.html = html(workspaceRoot);
}

export function refreshDashboardIfOpen(workspaceRoot: string | undefined): void {
  if (currentPanel) {
    currentPanel.webview.html = html(workspaceRoot);
  }
}
