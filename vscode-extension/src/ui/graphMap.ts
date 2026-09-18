import * as fs from "fs";
import * as vscode from "vscode";
import { graphHtmlPath } from "../engine/engine";
import { adaptGraphHtml } from "./graphHtml";

let panel: vscode.WebviewPanel | undefined;

export function openGraphMap(
  extensionUri: vscode.Uri,
  root: string,
  onOpenNode: (id: string) => void,
  reveal = true
): boolean {
  const file = graphHtmlPath(root);
  if (!fs.existsSync(file)) return false;

  const media = vscode.Uri.joinPath(extensionUri, "media");
  if (!panel) {
    panel = vscode.window.createWebviewPanel(
      "synapse.graphMap",
      "Synapse Graph Map",
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [media] }
    );
    panel.iconPath = vscode.Uri.joinPath(media, "synapse-icon.svg");
    panel.onDidDispose(() => (panel = undefined));
    panel.webview.onDidReceiveMessage((msg) => {
      if (msg && msg.type === "openNode" && typeof msg.id === "string") onOpenNode(msg.id);
    });
  } else if (reveal) {
    panel.reveal();
  }
  const visUri = panel.webview.asWebviewUri(vscode.Uri.joinPath(media, "vendor", "vis-network.min.js")).toString();
  panel.webview.html = adaptGraphHtml(fs.readFileSync(file, "utf8"), { visUri, cspSource: panel.webview.cspSource });
  return true;
}

/** Re-render the map if it's open (after the graph changes). */
export function refreshGraphMap(extensionUri: vscode.Uri, root: string, onOpenNode: (id: string) => void): void {
  if (panel) openGraphMap(extensionUri, root, onOpenNode, false);
}
