import * as vscode from "vscode";
import * as cliService from "./cliService";
import { loadGraph, degreeByNode, groupByCommunity } from "./graphModel";
import { brainSvg } from "./brandAssets";

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function statsSection(workspaceRoot: string | undefined): string {
  if (!workspaceRoot || !cliService.hasGraph(workspaceRoot)) {
    return `
      <div class="empty">No graph built yet.</div>
      <a class="btn primary block" href="command:synapse.rebuildGraph">Build Graph</a>`;
  }
  const graph = loadGraph(cliService.graphJsonPath(workspaceRoot));
  if (!graph) {
    return `<div class="empty">Graph exists but couldn't be read.</div>
      <a class="btn block" href="command:synapse.rebuildGraph">Rebuild</a>`;
  }
  const communities = groupByCommunity(graph).size;
  const degree = degreeByNode(graph);
  const topNode = [...degree.entries()].sort((a, b) => b[1] - a[1])[0];
  const topLabel = topNode
    ? escapeHtml(graph.nodes.find((n) => n.id === topNode[0])?.label ?? topNode[0])
    : "—";

  return `
    <div class="stat-grid">
      <div class="stat"><div class="v">${graph.nodes.length}</div><div class="l">nodes</div></div>
      <div class="stat"><div class="v">${graph.edges.length}</div><div class="l">edges</div></div>
      <div class="stat"><div class="v">${communities}</div><div class="l">communities</div></div>
      <div class="stat"><div class="v" title="${topLabel}">${topLabel}</div><div class="l">top node</div></div>
    </div>`;
}

function buildHtml(workspaceRoot: string | undefined): string {
  const csp = ["default-src 'none'", "style-src 'unsafe-inline'", "img-src data:"].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  :root {
    --bg: #0b0d0f;
    --panel: #141619;
    --border: #23262b;
    --text: #e6e8ea;
    --muted: #8a9099;
    --accent: #22c55e;
    --accent-dim: #14532d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 14px 12px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px;
  }
  .hero { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .hero h1 { margin: 0; font-size: 16px; font-weight: 700; color: var(--accent); letter-spacing: 0.02em; }
  .tagline { color: var(--muted); margin: 0 0 16px 0; font-size: 11px; line-height: 1.5; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 12px; }
  .card h2 { margin: 0 0 10px 0; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; }
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .stat { background: rgba(34,197,94,0.07); border: 1px solid var(--border); border-radius: 8px; padding: 8px; text-align: center; }
  .stat .v { font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat .l { font-size: 9px; color: var(--muted); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.05em; }
  .empty { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  .btn {
    display: block; width: 100%; text-align: center; padding: 7px 10px; margin-bottom: 6px;
    border-radius: 7px; text-decoration: none; font-size: 12px; font-weight: 600;
    border: 1px solid var(--border); color: var(--text); background: rgba(255,255,255,0.03);
  }
  .btn:last-child { margin-bottom: 0; }
  .btn:hover { background: rgba(255,255,255,0.07); }
  .btn.primary { background: var(--accent); color: #0b0d0f; border: none; }
  .btn.primary:hover { background: #16d16a; }
  ol.steps { margin: 0; padding-left: 16px; color: var(--muted); line-height: 1.7; font-size: 11px; }
  ol.steps b { color: var(--text); }
  .footnote { color: var(--muted); font-size: 10px; margin-top: 4px; line-height: 1.5; }
</style>
</head>
<body>
  <div class="hero">${brainSvg(20, "#22c55e")}<h1>Synapse</h1></div>
  <p class="tagline">Local knowledge graph for your AI assistant. No API key. Nothing leaves your machine.</p>

  <div class="card">
    <h2>Your Graph</h2>
    ${statsSection(workspaceRoot)}
  </div>

  <div class="card">
    <h2>Actions</h2>
    <a class="btn primary" href="command:synapse.rebuildGraph">Rebuild Graph</a>
    <a class="btn" href="command:synapse.updateGraph">Update Graph</a>
    <a class="btn" href="command:synapse.openGraphView">Open Visualization</a>
    <a class="btn" href="command:synapse.query">Query Codebase</a>
    <a class="btn" href="command:synapse.explainAtCursor">Explain at Cursor</a>
    <a class="btn" href="command:synapse.showOutput">Show Output Log</a>
  </div>

  <div class="card">
    <h2>How It Works</h2>
    <ol class="steps">
      <li><b>Build</b> — parsed locally, no LLM, no network.</li>
      <li><b>Explore</b> — God Nodes + Communities below, or the full visualization.</li>
      <li><b>Ask</b> — query in plain language, or explain the symbol at your cursor.</li>
      <li><b>Copilot uses it automatically</b> once built, with your own subscription.</li>
    </ol>
    <p class="footnote">Built on the open-source graphify engine. Your code and API keys are never sent anywhere by Synapse.</p>
  </div>
</body>
</html>`;
}

export class DashboardViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "synapseDashboardView";
  private view: vscode.WebviewView | undefined;

  constructor(private workspaceRoot: string | undefined) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: false,
      // Command links (the Quick Actions buttons) are blocked by default in
      // webviews unless explicitly allowed — without this, every button
      // silently does nothing on click.
      enableCommandUris: true,
    };
    this.refresh();
  }

  refresh(): void {
    if (!this.view) return;
    this.view.webview.html = buildHtml(this.workspaceRoot);
  }
}
