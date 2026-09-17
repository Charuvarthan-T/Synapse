import * as vscode from "vscode";
import * as fs from "fs";
import { graphHtmlPath } from "./cliService";
import { logError } from "./logger";
import { neuronSvg } from "./brandAssets";

let currentPanel: vscode.WebviewPanel | undefined;

const BRAND_STYLE = `
<style>
  body { padding-top: 44px !important; }
  #synapse-band {
    position: fixed; top: 0; left: 0; right: 0; height: 44px; z-index: 1000;
    display: flex; align-items: center; gap: 10px; padding: 0 16px;
    background: #141619; border-bottom: 1px solid #23262b;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #e6e8ea; box-shadow: 0 1px 8px rgba(0,0,0,0.4);
  }
  #synapse-band .brand { font-weight: 700; letter-spacing: 0.3px; font-size: 14px; color: #22c55e; }
  #synapse-band .tag { opacity: 0.7; font-size: 12px; }
</style>`;

const BRAND_BAND = `
<div id="synapse-band">
  ${neuronSvg(18, "#22c55e")}
  <span class="brand">Synapse</span>
  <span class="tag">local knowledge graph — no API key, nothing leaves your machine</span>
</div>`;

export function openGraphView(workspaceRoot: string): void {
  const htmlPath = graphHtmlPath(workspaceRoot);
  if (!fs.existsSync(htmlPath)) {
    vscode.window.showWarningMessage(
      "No graph visualization found yet. Run 'Synapse: Rebuild Graph' first."
    );
    return;
  }

  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Beside);
  } else {
    currentPanel = vscode.window.createWebviewPanel(
      "synapseGraphView",
      "Synapse: Knowledge Graph",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      }
    );
    currentPanel.onDidDispose(() => (currentPanel = undefined));
  }

  try {
    let html = fs.readFileSync(htmlPath, { encoding: "utf-8" });
    // graph.html is self-contained (data inlined) and only loads vis-network
    // from unpkg.com — add a CSP that permits exactly that, nothing else.
    const csp = [
      "default-src 'none'",
      "script-src 'unsafe-inline' https://unpkg.com",
      "style-src 'unsafe-inline'",
      "img-src data:",
      "connect-src https://unpkg.com",
    ].join("; ");
    if (html.includes("<head>")) {
      html = html.replace(
        "<head>",
        `<head><meta http-equiv="Content-Security-Policy" content="${csp}">${BRAND_STYLE}`
      );
    }
    if (html.includes("<body>")) {
      html = html.replace("<body>", `<body>${BRAND_BAND}`);
    }
    currentPanel.webview.html = html;
  } catch (err) {
    logError("failed to render graph webview", err);
    vscode.window.showErrorMessage("Synapse: failed to open graph visualization.");
  }
}
