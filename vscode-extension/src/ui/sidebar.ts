import * as vscode from "vscode";
import type { GraphStats } from "../graphModel";
import { WEBVIEW_COMMANDS } from "./resultsPanel";
import { webviewOptions, webviewShell } from "./webviewHtml";

export type Phase = "no-workspace" | "untrusted" | "empty" | "ready" | "error";

export interface SidebarState {
  phase: Phase;
  task: { title: string; detail?: string; cancellable: boolean } | null;
  error: string | null;
  stats: GraphStats | null;
  builtAt: number | null;
  ai: { label: string; available: boolean };
}

export interface SidebarHandlers {
  ask(text: string): void;
  search(text: string): void;
  openSymbol(id: string): void;
  cancel(): void;
}

export class SidebarProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "synapse.dashboard";
  private view?: vscode.WebviewView;
  private state?: SidebarState;

  constructor(private readonly extensionUri: vscode.Uri, private readonly handlers: SidebarHandlers) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = webviewOptions(this.extensionUri);
    view.webview.html = webviewShell(view.webview, this.extensionUri, {
      title: "Synapse",
      css: "sidebar.css",
      js: "sidebar.js",
    });
    view.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
    view.onDidChangeVisibility(() => view.visible && this.post());
    view.onDidDispose(() => (this.view = undefined));
  }

  update(state: SidebarState): void {
    this.state = state;
    this.post();
  }

  /** Reveal the sidebar and focus its question box. */
  async focusInput(mode: "ask" | "search"): Promise<void> {
    await vscode.commands.executeCommand(`${SidebarProvider.viewId}.focus`);
    void this.view?.webview.postMessage({ type: "focusInput", mode });
  }

  clearInput(): void {
    void this.view?.webview.postMessage({ type: "clearInput" });
  }

  private post(): void {
    if (this.view && this.state) void this.view.webview.postMessage({ type: "state", state: this.state });
  }

  private onMessage(msg: any): void {
    if (!msg || typeof msg.type !== "string") return;
    const text = typeof msg.text === "string" ? msg.text.trim().slice(0, 4000) : "";
    switch (msg.type) {
      case "ready":
        this.post();
        break;
      case "ask":
        if (text) this.handlers.ask(text);
        break;
      case "search":
        if (text) this.handlers.search(text);
        break;
      case "openSymbol":
        if (typeof msg.id === "string") this.handlers.openSymbol(msg.id);
        break;
      case "cancel":
        this.handlers.cancel();
        break;
      case "run":
        if (WEBVIEW_COMMANDS.has(msg.command)) void vscode.commands.executeCommand(msg.command);
        break;
    }
  }
}
