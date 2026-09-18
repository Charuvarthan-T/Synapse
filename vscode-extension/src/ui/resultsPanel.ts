import * as vscode from "vscode";
import { webviewOptions, webviewShell } from "./webviewHtml";

/** Commands a webview may trigger. Anything else it sends is ignored. */
export const WEBVIEW_COMMANDS = new Set([
  "synapse.buildGraph",
  "synapse.chooseModel",
  "synapse.enrichGraph",
  "synapse.openGraphMap",
  "synapse.showLog",
  "synapse.verifyCode",
  "workbench.action.files.openFolder",
  "workbench.trust.manage",
]);

export interface NavigationHandlers {
  openFile(file: string, line?: number): Promise<void>;
  openSymbol(query: { id?: string; label?: string }): Promise<void>;
}

/** The single editor-area panel that shows answers, searches, explanations
 * and verification reports. Reused across requests. */
export class ResultsPanel {
  private panel?: vscode.WebviewPanel;
  private pending?: unknown;
  private ready = false;

  constructor(private readonly extensionUri: vscode.Uri, private readonly nav: NavigationHandlers) {}

  show(view: unknown, preserveFocus = true): void {
    if (!this.panel) {
      this.ready = false;
      this.panel = vscode.window.createWebviewPanel(
        "synapse.results",
        "Synapse",
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus },
        { ...webviewOptions(this.extensionUri), retainContextWhenHidden: true }
      );
      this.panel.iconPath = vscode.Uri.joinPath(this.extensionUri, "media", "synapse-icon.svg");
      this.panel.webview.html = webviewShell(this.panel.webview, this.extensionUri, {
        title: "Synapse",
        css: "results.css",
        js: "results.js",
      });
      this.panel.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
      this.panel.onDidDispose(() => {
        this.panel = undefined;
        this.ready = false;
      });
    } else if (!this.panel.visible) {
      this.panel.reveal(undefined, preserveFocus);
    }
    this.pending = view;
    if (this.ready) void this.panel.webview.postMessage({ type: "show", view });
  }

  get isOpen(): boolean {
    return !!this.panel;
  }

  private async onMessage(msg: any): Promise<void> {
    if (!msg || typeof msg.type !== "string") return;
    switch (msg.type) {
      case "ready":
        this.ready = true;
        if (this.pending) void this.panel?.webview.postMessage({ type: "show", view: this.pending });
        break;
      case "open":
        if (typeof msg.file === "string") await this.nav.openFile(msg.file, typeof msg.line === "number" ? msg.line : undefined);
        break;
      case "openSymbol":
        if (typeof msg.label === "string") await this.nav.openSymbol({ label: msg.label });
        break;
      case "copy":
        if (typeof msg.text === "string") {
          await vscode.env.clipboard.writeText(msg.text);
          vscode.window.setStatusBarMessage("$(check) Answer copied", 2000);
        }
        break;
      case "run":
        if (WEBVIEW_COMMANDS.has(msg.command)) await vscode.commands.executeCommand(msg.command);
        break;
    }
  }

  dispose(): void {
    this.panel?.dispose();
  }
}
