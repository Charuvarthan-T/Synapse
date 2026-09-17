import * as vscode from "vscode";
import * as cliService from "./cliService";
import { initLogger, log, logError, showOutput } from "./logger";
import { initStatusBar, setBusy, setReady, setIdle, setError } from "./statusBar";
import { GraphTreeProvider } from "./graphTreeProvider";
import { openGraphView } from "./graphWebview";
import { registerLmTools } from "./lmTools";
import { primaryWorkspaceRoot, isWorkspaceTrusted } from "./workspaceUtils";
import { loadGraph } from "./graphModel";
import type { GraphNode } from "./graphModel";

// Simple re-entrancy guard: never run two graph-mutating CLI operations
// (extract/update) concurrently against the same output directory.
let operationInFlight = false;

async function withLock(fn: () => Promise<void>): Promise<void> {
  if (operationInFlight) {
    vscode.window.showInformationMessage("Graphify: a graph operation is already running.");
    return;
  }
  operationInFlight = true;
  try {
    await fn();
  } finally {
    operationInFlight = false;
  }
}

export function activate(context: vscode.ExtensionContext): void {
  initLogger(context);
  log("Graphify extension activating");

  const statusBar = initStatusBar(context);
  const root = primaryWorkspaceRoot();
  const treeProvider = new GraphTreeProvider(root);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("graphifyExplorer", treeProvider));

  registerLmTools(context);

  if (root && cliService.hasGraph(root)) {
    const graph = loadGraph(cliService.graphJsonPath(root));
    setReady(graph?.nodes.length);
  } else {
    setIdle();
  }

  async function doRebuild(): Promise<void> {
    if (!root) {
      vscode.window.showWarningMessage("Graphify: open a folder to build a knowledge graph.");
      return;
    }
    if (!isWorkspaceTrusted()) {
      vscode.window.showWarningMessage(
        "Graphify: this workspace is not trusted. Trust it to build a graph."
      );
      return;
    }
    await withLock(async () => {
      setBusy("provisioning...");
      const provision = await cliService.ensureProvisioned((msg) => setBusy(msg));
      if (!provision.ok) {
        setError(provision.detail);
        logError("provisioning failed", provision.detail);
        const action = await vscode.window.showErrorMessage(
          `Graphify could not set up its Python environment: ${provision.detail}`,
          "Show Log"
        );
        if (action === "Show Log") showOutput();
        return;
      }

      setBusy("building graph (local, no API key)...");
      const result = await cliService.extract(root);
      if (result.code !== 0) {
        setError(result.stderr);
        logError("extract failed", result.stderr);
        vscode.window.showErrorMessage("Graphify: failed to build graph. See output log for details.");
        return;
      }
      log(result.stdout);
      treeProvider.refresh();
      const graph = loadGraph(cliService.graphJsonPath(root));
      setReady(graph?.nodes.length);
      vscode.window.showInformationMessage("Graphify: knowledge graph built.");
    });
  }

  async function doUpdate(): Promise<void> {
    if (!root || !cliService.hasGraph(root)) return doRebuild();
    if (!isWorkspaceTrusted()) return;
    await withLock(async () => {
      setBusy("updating graph...");
      const result = await cliService.update(root);
      if (result.code !== 0) {
        logError("update failed", result.stderr);
        // Non-fatal: keep showing the last-known-good graph rather than an error state.
        setReady();
        return;
      }
      log(result.stdout);
      treeProvider.refresh();
      const graph = loadGraph(cliService.graphJsonPath(root));
      setReady(graph?.nodes.length);
    });
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("graphify.rebuildGraph", doRebuild),
    vscode.commands.registerCommand("graphify.updateGraph", doUpdate),
    vscode.commands.registerCommand("graphify.showOutput", showOutput),

    vscode.commands.registerCommand("graphify.openGraphView", () => {
      if (!root) return;
      openGraphView(root);
    }),

    vscode.commands.registerCommand("graphify.revealNode", async (node: GraphNode) => {
      if (!root || !node?.source_file) return;
      try {
        const uri = vscode.Uri.joinPath(vscode.Uri.file(root), node.source_file);
        const lineMatch = node.source_location?.match(/L(\d+)/);
        const line = lineMatch ? Math.max(0, parseInt(lineMatch[1], 10) - 1) : 0;
        const doc = await vscode.workspace.openTextDocument(uri);
        const editor = await vscode.window.showTextDocument(doc);
        const pos = new vscode.Position(line, 0);
        editor.selection = new vscode.Selection(pos, pos);
        editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
      } catch (err) {
        logError(`failed to reveal ${node.source_file}`, err);
        vscode.window.showWarningMessage(`Graphify: could not open ${node.source_file}.`);
      }
    }),

    vscode.commands.registerCommand("graphify.query", async () => {
      if (!root || !cliService.hasGraph(root)) {
        vscode.window.showWarningMessage("Graphify: build the graph first.");
        return;
      }
      const question = await vscode.window.showInputBox({
        prompt: "Ask a question about this codebase",
        placeHolder: "e.g. how does authentication work",
      });
      if (!question) return;
      const budget = vscode.workspace.getConfiguration("graphify").get<number>("queryBudget", 2000);
      setBusy("querying...");
      const result = await cliService.query(root, question, budget);
      setReady();
      if (result.code !== 0) {
        vscode.window.showErrorMessage("Graphify: query failed. See output log.");
        logError("query command failed", result.stderr);
        return;
      }
      const doc = await vscode.workspace.openTextDocument({
        content: result.stdout || "No relevant nodes found.",
        language: "plaintext",
      });
      await vscode.window.showTextDocument(doc, { preview: true });
    }),

    vscode.commands.registerCommand("graphify.explainAtCursor", async () => {
      if (!root || !cliService.hasGraph(root)) {
        vscode.window.showWarningMessage("Graphify: build the graph first.");
        return;
      }
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const range = editor.document.getWordRangeAtPosition(editor.selection.active);
      if (!range) {
        vscode.window.showWarningMessage("Graphify: place the cursor on a symbol first.");
        return;
      }
      const symbol = editor.document.getText(range);
      setBusy(`explaining ${symbol}...`);
      const result = await cliService.explain(root, symbol);
      setReady();
      if (result.code !== 0) {
        vscode.window.showErrorMessage("Graphify: explain failed. See output log.");
        logError("explain command failed", result.stderr);
        return;
      }
      const doc = await vscode.workspace.openTextDocument({
        content: result.stdout || `No node named '${symbol}' found.`,
        language: "plaintext",
      });
      await vscode.window.showTextDocument(doc, { preview: true });
    })
  );

  // Auto-build on open.
  const autoBuild = vscode.workspace.getConfiguration("graphify").get<boolean>("autoBuildOnOpen", true);
  if (autoBuild && root && isWorkspaceTrusted() && !cliService.hasGraph(root)) {
    void doRebuild();
  }

  // Debounced auto-update on save.
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const autoUpdate = vscode.workspace
        .getConfiguration("graphify")
        .get<boolean>("autoUpdateOnSave", true);
      if (!autoUpdate || doc.languageId !== "python" || !root || !isWorkspaceTrusted()) return;
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(() => void doUpdate(), 2000);
    })
  );

  log("Graphify extension activated");
}

export function deactivate(): void {
  // Child processes spawned via cliService are short-lived (one CLI
  // invocation each) and not tracked here, so there is nothing to tear down.
}
