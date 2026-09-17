import * as vscode from "vscode";

let item: vscode.StatusBarItem | undefined;

export function initStatusBar(context: vscode.ExtensionContext): vscode.StatusBarItem {
  item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.command = "synapse.openDashboard";
  context.subscriptions.push(item);
  setIdle();
  item.show();
  return item;
}

export function setBusy(message: string): void {
  if (!item) return;
  item.text = `$(sync~spin) Synapse: ${message}`;
  item.tooltip = message;
}

export function setReady(nodeCount?: number): void {
  if (!item) return;
  item.text = `$(check) Synapse${nodeCount !== undefined ? `: ${nodeCount} nodes` : ""}`;
  item.tooltip = "Graph is up to date. Click to open the Dashboard.";
}

export function setIdle(): void {
  if (!item) return;
  item.text = "$(circle-outline) Synapse: not built";
  item.tooltip = "Click to open the Dashboard and build the knowledge graph.";
}

export function setError(message: string): void {
  if (!item) return;
  item.text = "$(error) Synapse: error";
  item.tooltip = message;
}
