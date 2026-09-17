import * as vscode from "vscode";

/** v1 supports a single primary workspace root (the first folder). Multi-root
 * per-folder graphs are a documented follow-up, not handled here. */
export function primaryWorkspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

export function isWorkspaceTrusted(): boolean {
  return vscode.workspace.isTrusted;
}
