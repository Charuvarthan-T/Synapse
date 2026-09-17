import * as vscode from "vscode";
import * as fs from "fs";
import { graphHtmlPath } from "./cliService";
import { logError } from "./logger";

let currentPanel: vscode.WebviewPanel | undefined;

export function openGraphView(workspaceRoot: string): void {
  const htmlPath = graphHtmlPath(workspaceRoot);
  if (!fs.existsSync(htmlPath)) {
    vscode.window.showWarningMessage(
      "No graph visualization found yet. Run 'Graphify: Rebuild Graph' first."
    );
    return;
  }

  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Beside);
  } else {
    currentPanel = vscode.window.createWebviewPanel(
      "graphifyGraphView",
      "Graphify: Knowledge Graph",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      }
    );
    currentPanel.onDidDispose(() => (currentPanel = undefined));
  }

  try {
    const html = fs.readFileSync(htmlPath, { encoding: "utf-8" });
    // graph.html is self-contained (data inlined) and only loads vis-network
    // from unpkg.com — add a CSP that permits exactly that, nothing else.
    const csp = [
      "default-src 'none'",
      "script-src 'unsafe-inline' https://unpkg.com",
      "style-src 'unsafe-inline'",
      "img-src data:",
      "connect-src https://unpkg.com",
    ].join("; ");
    const withCsp = html.includes("<head>")
      ? html.replace(
          "<head>",
          `<head><meta http-equiv="Content-Security-Policy" content="${csp}">`
        )
      : html;
    currentPanel.webview.html = withCsp;
  } catch (err) {
    logError("failed to render graph webview", err);
    vscode.window.showErrorMessage("Graphify: failed to open graph visualization.");
  }
}
