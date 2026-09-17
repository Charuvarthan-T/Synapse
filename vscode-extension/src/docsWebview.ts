import * as vscode from "vscode";
import { neuronSvg } from "./brandAssets";

let currentPanel: vscode.WebviewPanel | undefined;

function html(): string {
  const csp = ["default-src 'none'", "style-src 'unsafe-inline'", "img-src data:"].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>Synapse Documentation</title>
<style>
  :root {
    --bg: #0b0d0f;
    --panel: #141619;
    --border: #23262b;
    --text: #e6e8ea;
    --muted: #8a9099;
    --accent: #22c55e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px 24px 80px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 760px; margin: 0 auto; }
  .hero { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .hero h1 { margin: 0; font-size: 24px; font-weight: 700; color: var(--accent); }
  .tagline { color: var(--muted); margin: 0 0 36px 0; font-size: 14px; }
  section { margin-bottom: 28px; padding-bottom: 28px; border-bottom: 1px solid var(--border); }
  section:last-child { border-bottom: none; }
  h2 { font-size: 16px; margin: 0 0 10px 0; display: flex; align-items: center; gap: 8px; }
  h2 .badge {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
  }
  p, li { font-size: 13px; line-height: 1.7; color: var(--text); }
  p.muted { color: var(--muted); }
  code {
    background: var(--panel); border: 1px solid var(--border); border-radius: 4px;
    padding: 1px 6px; font-size: 12px; color: var(--accent);
  }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { text-align: left; padding: 8px 10px; font-size: 12px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
  .toc { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 36px; }
  .toc a {
    font-size: 12px; color: var(--muted); text-decoration: none; border: 1px solid var(--border);
    border-radius: 20px; padding: 5px 12px; background: var(--panel);
  }
  .toc a:hover { color: var(--accent); border-color: var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">${neuronSvg(28, "#22c55e")}<h1>Synapse Documentation</h1></div>
  <p class="tagline">A local knowledge graph of your code, for your AI assistant. No API key. Nothing leaves your machine.</p>

  <div class="toc">
    <a href="#dashboard">Dashboard</a>
    <a href="#sidebar">Sidebar</a>
    <a href="#visualization">Visualization</a>
    <a href="#query">Query</a>
    <a href="#explain">Explain</a>
    <a href="#copilot">Copilot Chat</a>
    <a href="#settings">Settings</a>
    <a href="#troubleshooting">Troubleshooting</a>
  </div>

  <section id="dashboard">
    <h2><span class="badge"></span>Dashboard</h2>
    <p>The Dashboard lives in the sidebar — click the Synapse icon in the Activity Bar and it's the first thing you see.</p>
    <ul>
      <li><b>Your Graph</b> shows live stats: node count, edge count, community count, and your codebase's most-connected symbol.</li>
      <li><b>Actions</b> are one-click shortcuts to every command below — you never need the Command Palette for day-to-day use.</li>
    </ul>
  </section>

  <section id="sidebar">
    <h2><span class="badge"></span>Sidebar: God Nodes &amp; Communities</h2>
    <p>Below the Dashboard, the <b>Knowledge Graph</b> view lets you browse the graph directly:</p>
    <ul>
      <li><b>God Nodes</b> — the most-connected functions/classes in your codebase. These are usually your core abstractions, the things everything else depends on.</li>
      <li><b>Communities</b> — clusters of related code, detected automatically from how the code actually calls and imports itself (not just folder structure).</li>
    </ul>
    <p class="muted">Click any item to jump straight to its definition in the editor.</p>
  </section>

  <section id="visualization">
    <h2><span class="badge"></span>Graph Visualization</h2>
    <p><code>Synapse: Open Graph Visualization</code> opens an interactive node/edge diagram of your entire codebase in a side panel. Drag nodes, zoom, search by name, and click a node to inspect its connections — useful for getting an at-a-glance feel for a codebase you don't know yet.</p>
  </section>

  <section id="query">
    <h2><span class="badge"></span>Query Codebase</h2>
    <p><code>Synapse: Query Codebase</code> lets you ask a plain-language question (e.g. <i>"how does authentication work"</i>) and get back the functions, classes, and relationships actually relevant to it — a compact answer instead of having to read whole files yourself.</p>
  </section>

  <section id="explain">
    <h2><span class="badge"></span>Explain Symbol at Cursor</h2>
    <p>Place your cursor on any function or class name and run <code>Synapse: Explain Symbol at Cursor</code> for a focused look at just that symbol and its direct neighbors (what calls it, what it calls, what it imports).</p>
  </section>

  <section id="copilot">
    <h2><span class="badge"></span>GitHub Copilot Chat Integration</h2>
    <p>Once your graph is built, Copilot Chat's <b>agent mode</b> can call Synapse automatically — it decides on its own when looking up codebase context would help, using <b>your existing Copilot subscription</b>. There is nothing to configure.</p>
    <p class="muted">Synapse never sees your Copilot account or any API key. It only ever runs locally and returns plain text back to Copilot when asked.</p>
  </section>

  <section id="settings">
    <h2><span class="badge"></span>Settings</h2>
    <table>
      <tr><th>Setting</th><th>Default</th><th>What it does</th></tr>
      <tr><td><code>synapse.autoBuildOnOpen</code></td><td>true</td><td>Build the graph automatically on opening a Python workspace</td></tr>
      <tr><td><code>synapse.autoUpdateOnSave</code></td><td>true</td><td>Incrementally refresh the graph after saving a Python file</td></tr>
      <tr><td><code>synapse.pythonPath</code></td><td>"" (auto-detect)</td><td>Explicit path to a Python 3.10+ interpreter</td></tr>
      <tr><td><code>synapse.queryBudget</code></td><td>2000</td><td>Max tokens included per query response</td></tr>
      <tr><td><code>synapse.devPackagePath</code></td><td>""</td><td>Install the graphify engine from a local path instead of PyPI (development only)</td></tr>
    </table>
  </section>

  <section id="troubleshooting">
    <h2><span class="badge"></span>Troubleshooting</h2>
    <p>If anything seems stuck or wrong, run <code>Synapse: Show Output Log</code> first — every command Synapse runs against the graphify engine, and its full output, is logged there.</p>
    <p class="muted">Synapse is built on top of the open-source graphify engine and requires Python 3.10+ on your PATH for its one-time, fully local setup.</p>
  </section>
</div>
</body>
</html>`;
}

export function openDocs(): void {
  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Active);
    return;
  }
  currentPanel = vscode.window.createWebviewPanel(
    "synapseDocs",
    "Synapse: Documentation",
    vscode.ViewColumn.Active,
    { enableScripts: false, retainContextWhenHidden: true }
  );
  currentPanel.webview.html = html();
  currentPanel.onDidDispose(() => (currentPanel = undefined));
}
