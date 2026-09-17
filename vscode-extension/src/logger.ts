import * as vscode from "vscode";

let channel: vscode.OutputChannel | undefined;

export function initLogger(context: vscode.ExtensionContext): void {
  channel = vscode.window.createOutputChannel("Graphify");
  context.subscriptions.push(channel);
}

export function log(message: string): void {
  const line = `[${new Date().toISOString()}] ${message}`;
  channel?.appendLine(line);
}

export function logError(message: string, err: unknown): void {
  const detail = err instanceof Error ? err.message : String(err);
  log(`ERROR: ${message}: ${detail}`);
}

export function showOutput(): void {
  channel?.show(true);
}
