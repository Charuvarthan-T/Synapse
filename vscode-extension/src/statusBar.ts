import * as vscode from "vscode";

let item: vscode.StatusBarItem | undefined;

export function initStatusBar(context: vscode.ExtensionContext): vscode.StatusBarItem {
  item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.command = "graphify.rebuildGraph";
  context.subscriptions.push(item);
  setIdle();
  item.show();
  return item;
}

export function setBusy(message: string): void {
  if (!item) return;
  item.text = `$(sync~spin) Graphify: ${message}`;
  item.tooltip = message;
}

export function setReady(nodeCount?: number): void {
  if (!item) return;
  item.text = `$(check) Graphify${nodeCount !== undefined ? `: ${nodeCount} nodes` : ""}`;
  item.tooltip = "Graph is up to date. Click to rebuild.";
}

export function setIdle(): void {
  if (!item) return;
  item.text = "$(circle-outline) Graphify: not built";
  item.tooltip = "Click to build the knowledge graph for this workspace.";
}

export function setError(message: string): void {
  if (!item) return;
  item.text = "$(error) Graphify: error";
  item.tooltip = message;
}
