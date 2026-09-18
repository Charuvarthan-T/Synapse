import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { Engine, EngineError, graphPath } from "./engine/engine";
import { CompletionHandler } from "./ai/bridgeServer";
import { chooseModel, describeProvider, resolveProvider } from "./ai/providers";
import { GraphCache, lineOf, setGraphModelLogger } from "./graphModel";
import { EXCLUDE_GLOB, PROJECT_GLOB, isCodeFile } from "./languages";
import { registerLmTools } from "./lmTools";
import { initLogger, log, logError, showOutput } from "./logger";
import { openGraphMap, refreshGraphMap } from "./ui/graphMap";
import { answerView, explainView, NodeLookup, searchView, verifyView } from "./ui/presenters";
import { ResultsPanel } from "./ui/resultsPanel";
import { SidebarProvider, SidebarState } from "./ui/sidebar";

interface Task {
  title: string;
  detail?: string;
  cancellable: boolean;
  exclusive: boolean;
  abort: AbortController;
}

/** Test-only hooks (available when the extension runs under the test host). */
export interface SynapseTestApi {
  engine: Engine;
  state(): SidebarState;
  /** Replace the AI provider with a fake; pass undefined to restore. */
  setAiOverride(handler: CompletionHandler | undefined): void;
  lastView(): unknown;
}

const MAX_VERIFY_CHARS = 200_000;

export function activate(context: vscode.ExtensionContext): SynapseTestApi | undefined {
  initLogger(context);
  setGraphModelLogger(log);
  const version = context.extension.packageJSON.version as string;
  log(`Synapse ${version} activating`);

  const root = (): string | undefined => vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const config = () => vscode.workspace.getConfiguration("synapse");
  const home = process.env.SYNAPSE_HOME || path.join(os.homedir(), ".synapse");

  const engine = new Engine(() => ({
    extensionPath: context.extensionPath,
    home,
    pythonPath: config().get<string>("pythonPath", "").trim() || undefined,
    devPackagePath: config().get<string>("devPackagePath", "").trim() || undefined,
    log,
  }));
  const graph = new GraphCache(() => (root() ? graphPath(root()!) : undefined));

  // --- State -----------------------------------------------------------------

  const tasks: Task[] = [];
  let lastError: string | null = null;
  let aiInfo = { label: "Checking…", available: false };
  let aiOverride: CompletionHandler | undefined;
  let lastView: unknown;

  const statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusItem.command = `${SidebarProvider.viewId}.focus`;
  statusItem.name = "Synapse";
  context.subscriptions.push(statusItem);

  const results = new ResultsPanel(context.extensionUri, {
    openFile: (file, line) => openFile(file, line),
    openSymbol: (q) => openSymbol(q),
  });
  const sidebar = new SidebarProvider(context.extensionUri, {
    ask: (text) => void ask(text),
    search: (text) => void search(text),
    openSymbol: (id) => void openSymbol({ id }),
    cancel: () => [...tasks].reverse().find((t) => t.cancellable)?.abort.abort(),
  });
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(SidebarProvider.viewId, sidebar, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    { dispose: () => results.dispose() }
  );

  function currentState(): SidebarState {
    const r = root();
    const stats = r && vscode.workspace.isTrusted ? graph.getStats() : null;
    let builtAt: number | null = null;
    try {
      builtAt = r && stats ? fs.statSync(graphPath(r)).mtimeMs : null;
    } catch {
      builtAt = null;
    }
    const task = tasks.find((t) => t.exclusive) ?? tasks[tasks.length - 1];
    const phase = !r ? "no-workspace" : !vscode.workspace.isTrusted ? "untrusted" : lastError ? "error" : stats ? "ready" : "empty";
    return {
      phase,
      task: task ? { title: task.title, detail: task.detail, cancellable: task.cancellable } : null,
      error: lastError,
      stats,
      builtAt,
      ai: aiInfo,
    };
  }

  function refresh(): void {
    const state = currentState();
    sidebar.update(state);
    void vscode.commands.executeCommand("setContext", "synapse.hasGraph", !!state.stats);
    const task = state.task;
    if (!root()) {
      statusItem.hide();
      return;
    }
    if (task) {
      statusItem.text = `$(sync~spin) Synapse`;
      statusItem.tooltip = `${task.title}${task.detail ? ` · ${task.detail}` : ""}`;
    } else if (state.phase === "error") {
      statusItem.text = "$(warning) Synapse";
      statusItem.tooltip = lastError ?? "Synapse hit an error";
    } else if (state.stats) {
      statusItem.text = "$(pass) Synapse";
      statusItem.tooltip = `Graph ready: ${state.stats.nodes.toLocaleString()} symbols, ${state.stats.edges.toLocaleString()} relations`;
    } else {
      statusItem.text = "$(circle-large-outline) Synapse";
      statusItem.tooltip = "No graph yet. Click to open Synapse.";
    }
    statusItem.show();
  }

  async function refreshAi(): Promise<void> {
    aiInfo = aiOverride ? { label: "Test provider", available: true } : await describeProvider();
    refresh();
  }

  // --- Tasks -----------------------------------------------------------------

  async function runTask<T>(
    opts: { title: string; detail?: string; exclusive: boolean; cancellable?: boolean },
    fn: (task: Task, report: (detail: string) => void) => Promise<T>
  ): Promise<T | undefined> {
    const running = tasks.find((t) => t.exclusive);
    if (opts.exclusive && running) {
      vscode.window.showInformationMessage(`Synapse is busy: ${running.title.toLowerCase()}.`);
      return undefined;
    }
    const task: Task = {
      title: opts.title,
      detail: opts.detail,
      exclusive: opts.exclusive,
      cancellable: opts.cancellable ?? true,
      abort: new AbortController(),
    };
    tasks.push(task);
    refresh();
    try {
      return await fn(task, (detail) => {
        task.detail = detail;
        refresh();
      });
    } finally {
      tasks.splice(tasks.indexOf(task), 1);
      refresh();
      if (opts.exclusive && pendingUpdate) {
        pendingUpdate = false;
        scheduleUpdate(0);
      }
    }
  }

  function isCancel(err: unknown): boolean {
    return err instanceof EngineError && err.kind === "cancelled";
  }

  function errorMessage(err: unknown): string {
    if (err instanceof EngineError) {
      if (err.detail) log(err.detail);
      return err.message;
    }
    return err instanceof Error ? err.message : String(err);
  }

  function requireWorkspace(): string | undefined {
    const r = root();
    if (!r) {
      vscode.window.showWarningMessage("Synapse: open a folder first.");
      return undefined;
    }
    if (!vscode.workspace.isTrusted) {
      vscode.window.showWarningMessage("Synapse only runs in trusted workspaces.");
      return undefined;
    }
    return r;
  }

  function requireGraph(): string | undefined {
    const r = requireWorkspace();
    if (!r) return undefined;
    if (!graph.getStats()) {
      void vscode.window
        .showInformationMessage("Synapse: build the knowledge graph first.", "Build Graph")
        .then((a) => a && vscode.commands.executeCommand("synapse.buildGraph"));
      return undefined;
    }
    return r;
  }

  // --- Graph building ---------------------------------------------------------

  async function build(): Promise<void> {
    const r = requireWorkspace();
    if (!r) return;
    await runTask({ title: "Building graph", detail: "Starting", exclusive: true }, async (task, report) => {
      try {
        await engine.build(r, task.abort.signal, report);
        lastError = null;
        const stats = graph.getStats();
        log(`graph built: ${stats?.nodes} nodes, ${stats?.edges} edges`);
      } catch (err) {
        if (isCancel(err)) return;
        lastError = errorMessage(err);
        logError("build failed", err);
        const action = await vscode.window.showErrorMessage(`Synapse: ${lastError}`, "Show Log");
        if (action) showOutput();
      }
    });
  }

  let updateTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingUpdate = false;

  function scheduleUpdate(delay = 1500): void {
    if (updateTimer) clearTimeout(updateTimer);
    updateTimer = setTimeout(() => void update(), delay);
  }

  async function update(): Promise<void> {
    const r = root();
    if (!r || !vscode.workspace.isTrusted || !graph.getStats()) return;
    if (tasks.some((t) => t.exclusive)) {
      pendingUpdate = true;
      return;
    }
    await runTask({ title: "Updating graph", exclusive: true, cancellable: false }, async (task) => {
      try {
        await engine.update(r, task.abort.signal);
        lastError = null;
      } catch (err) {
        // Keep serving the last good graph; details are in the log.
        logError("incremental update failed", err);
      }
    });
  }

  // --- AI ----------------------------------------------------------------------

  async function provider(): Promise<{ label: string; complete: CompletionHandler }> {
    if (aiOverride) return { label: "Test provider", complete: aiOverride };
    return resolveProvider();
  }

  const lookup: NodeLookup = {
    byId(id) {
      const n = graph.findNode(id);
      return n ? { label: n.label, file: n.source_file, line: lineOf(n.source_location) } : undefined;
    },
    hasLabel: (label) => graph.findByLabel(label).length > 0,
  };

  function showView(view: unknown): void {
    lastView = view;
    results.show(view);
  }

  function aiErrorView(mode: string, title: string, message: string) {
    return {
      kind: "error",
      mode,
      title,
      message,
      actions: [
        { label: "Choose AI Model", command: "synapse.chooseModel" },
        { label: "Show Log", command: "synapse.showLog" },
      ],
    };
  }

  async function ask(question: string): Promise<void> {
    const r = requireGraph();
    if (!r) return;
    let ai: { label: string; complete: CompletionHandler };
    try {
      ai = await provider();
    } catch (err) {
      showView(aiErrorView("ask", question, errorMessage(err)));
      return;
    }
    sidebar.clearInput();
    const steps = ["Retrieving context from the graph", `Asking ${ai.label}`, "Checking claims against the graph", "Revising if the graph disagrees"];
    const loading = (current: number) => showView({ kind: "loading", mode: "ask", title: question, subtitle: ai.label, steps, current });
    loading(0);
    await runTask({ title: "Answering", detail: ai.label, exclusive: false }, async (task) => {
      try {
        const result = await engine.reason(r, question, ai.complete, task.abort.signal, (n) => loading(n === 1 ? 1 : 3));
        showView(answerView(result, ai.label, lookup));
      } catch (err) {
        if (isCancel(err)) {
          showView({ kind: "error", mode: "ask", title: question, message: "Cancelled." });
          return;
        }
        logError("ask failed", err);
        showView(
          err instanceof EngineError && err.kind === "ai"
            ? aiErrorView("ask", question, err.message)
            : { kind: "error", mode: "ask", title: question, message: errorMessage(err), actions: [{ label: "Show Log", command: "synapse.showLog" }] }
        );
      }
    });
    void refreshAi();
  }

  async function search(question: string): Promise<void> {
    const r = requireGraph();
    if (!r) return;
    sidebar.clearInput();
    showView({ kind: "loading", mode: "search", title: question, subtitle: "Local graph search", steps: ["Traversing the graph"], current: 0 });
    await runTask({ title: "Searching", exclusive: false }, async (task) => {
      try {
        const result = await engine.search(r, question, config().get<number>("queryBudget", 2000), task.abort.signal);
        showView(searchView(question, result));
      } catch (err) {
        if (isCancel(err)) return;
        showView({ kind: "error", mode: "search", title: question, message: errorMessage(err), actions: [{ label: "Show Log", command: "synapse.showLog" }] });
      }
    });
  }

  async function explainSymbol(): Promise<void> {
    const r = requireGraph();
    if (!r) return;
    const editor = vscode.window.activeTextEditor;
    let symbol = "";
    if (editor) {
      symbol = editor.selection.isEmpty
        ? editor.document.getText(editor.document.getWordRangeAtPosition(editor.selection.active))
        : editor.document.getText(editor.selection);
    }
    symbol = symbol.trim();
    if (!symbol || symbol.length > 200 || /\s/.test(symbol)) {
      symbol =
        (await vscode.window.showInputBox({
          title: "Synapse: Explain Symbol",
          prompt: "Function, class or file name",
          value: symbol && symbol.length <= 200 ? symbol.split(/\s+/)[0] : "",
        }))?.trim() ?? "";
    }
    if (!symbol) return;
    showView({ kind: "loading", mode: "explain", title: symbol, steps: ["Looking up the symbol"], current: 0 });
    await runTask({ title: "Explaining", detail: symbol, exclusive: false }, async (task) => {
      try {
        showView(explainView(symbol, await engine.explain(r, symbol, task.abort.signal)));
      } catch (err) {
        if (isCancel(err)) return;
        showView({ kind: "error", mode: "explain", title: symbol, message: errorMessage(err) });
      }
    });
  }

  async function verifyCode(): Promise<void> {
    const r = requireGraph();
    if (!r) return;
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showInformationMessage("Synapse: open a file (or select code) to verify.");
      return;
    }
    const doc = editor.document;
    const range = editor.selection.isEmpty ? undefined : editor.selection;
    const code = doc.getText(range);
    if (!code.trim()) {
      vscode.window.showInformationMessage("Synapse: there is no code to verify.");
      return;
    }
    if (code.length > MAX_VERIFY_CHARS) {
      vscode.window.showWarningMessage("Synapse: select a smaller piece of code to verify (up to 200 KB).");
      return;
    }
    const name = path.basename(doc.fileName);
    const source = range ? `${name}, lines ${range.start.line + 1}–${range.end.line + 1}` : name;
    const lines = range ? range.end.line - range.start.line + 1 : doc.lineCount;
    showView({ kind: "loading", mode: "verify", title: source, steps: ["Checking imports, calls and methods against the graph"], current: 0 });
    await runTask({ title: "Verifying code", exclusive: false }, async (task) => {
      try {
        showView(verifyView(await engine.verify(r, code, task.abort.signal), source, lines, lookup));
      } catch (err) {
        if (isCancel(err)) return;
        showView({ kind: "error", mode: "verify", title: source, message: errorMessage(err) });
      }
    });
  }

  async function enrich(): Promise<void> {
    const r = requireGraph();
    if (!r) return;
    let ai: { label: string; complete: CompletionHandler };
    try {
      ai = await provider();
    } catch (err) {
      const pick = await vscode.window.showWarningMessage(errorMessage(err), "Choose AI Model");
      if (pick) await vscode.commands.executeCommand("synapse.chooseModel");
      return;
    }
    const units = graph.getStats()?.codeUnits ?? 0;
    const batches = Math.max(1, Math.ceil(units / 20));
    const go = await vscode.window.showInformationMessage(
      `Enrich the graph with AI-inferred intent relations (handles, validates, manages…)?`,
      {
        modal: true,
        detail:
          `${ai.label} will be asked about ${units.toLocaleString()} symbols in about ${batches.toLocaleString()} ` +
          `request${batches === 1 ? "" : "s"}. Only symbol names and file locations are sent, not your source code. ` +
          `Relations are validated before they're added, and never replace structural ones.`,
      },
      "Enrich"
    );
    if (go !== "Enrich") return;
    await runTask({ title: "Enriching with AI", detail: `0 of ~${batches} requests`, exclusive: true }, async (task, report) => {
      try {
        const res = await engine.enrich(r, ai.complete, task.abort.signal, (n) => report(`${Math.min(n, batches)} of ~${batches} requests`));
        const added = res.edgesAdded + res.edgesAugmented;
        const failed = res.failedRequests ? ` ${res.failedRequests} request${res.failedRequests === 1 ? "" : "s"} failed; see the log.` : "";
        if (added) {
          vscode.window.showInformationMessage(`Synapse: added ${added.toLocaleString()} AI-inferred relations to the graph.${failed}`);
        } else {
          vscode.window.showWarningMessage(`Synapse: the AI didn't return any relations that passed validation.${failed}`);
        }
        refreshGraphMap(context.extensionUri, r, (id) => void openSymbol({ id }));
      } catch (err) {
        if (isCancel(err)) return;
        logError("enrich failed", err);
        const pick = await vscode.window.showErrorMessage(`Synapse: ${errorMessage(err)}`, "Show Log");
        if (pick) showOutput();
      }
    });
    void refreshAi();
  }

  // --- Navigation ---------------------------------------------------------------

  async function openFile(file: string, line?: number): Promise<void> {
    const r = root();
    if (!r) return;
    const full = path.resolve(r, file);
    const rel = path.relative(r, full);
    if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) {
      log(`refusing to open a path outside the workspace: ${file}`);
      return;
    }
    try {
      const editor = await vscode.window.showTextDocument(vscode.Uri.file(full), { preview: true, viewColumn: vscode.ViewColumn.One });
      if (line && line > 0) {
        const pos = new vscode.Position(Math.min(line - 1, editor.document.lineCount - 1), 0);
        editor.selection = new vscode.Selection(pos, pos);
        editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
      }
    } catch (err) {
      logError(`could not open ${file}`, err);
      vscode.window.showWarningMessage(`Synapse: couldn't open ${file}.`);
    }
  }

  async function openSymbol(q: { id?: string; label?: string }): Promise<void> {
    let nodes = q.id ? [graph.findNode(q.id)].filter((n) => !!n) : graph.findByLabel(q.label ?? "");
    nodes = nodes.filter((n) => n!.source_file);
    if (!nodes.length) {
      vscode.window.setStatusBarMessage(`Synapse: no source location for ${q.label ?? q.id}`, 3000);
      return;
    }
    let node = nodes[0]!;
    if (nodes.length > 1) {
      const pick = await vscode.window.showQuickPick(
        nodes.map((n) => ({ label: n!.label, description: `${n!.source_file}${n!.source_location ? `:${lineOf(n!.source_location)}` : ""}`, node: n! })),
        { title: `Synapse: ${nodes.length} matches for ${q.label}` }
      );
      if (!pick) return;
      node = pick.node;
    }
    await openFile(node.source_file!, lineOf(node.source_location));
  }

  // --- Commands ---------------------------------------------------------------------

  const promptFor = async (mode: "ask" | "search") => {
    const text = await vscode.window.showInputBox({
      title: mode === "ask" ? "Synapse: Ask About This Codebase" : "Synapse: Search Graph",
      prompt: mode === "ask" ? "Answered by your AI, grounded in and checked against the code graph" : "Local graph search. No AI is used.",
      placeHolder: mode === "ask" ? "How does authentication work?" : "authentication",
    });
    if (text?.trim()) await (mode === "ask" ? ask(text.trim()) : search(text.trim()));
  };

  context.subscriptions.push(
    vscode.commands.registerCommand("synapse.buildGraph", build),
    vscode.commands.registerCommand("synapse.ask", (text?: string) => (typeof text === "string" && text.trim() ? ask(text.trim()) : promptFor("ask"))),
    vscode.commands.registerCommand("synapse.search", (text?: string) => (typeof text === "string" && text.trim() ? search(text.trim()) : promptFor("search"))),
    vscode.commands.registerCommand("synapse.explainSymbol", explainSymbol),
    vscode.commands.registerCommand("synapse.verifyCode", verifyCode),
    vscode.commands.registerCommand("synapse.enrichGraph", enrich),
    vscode.commands.registerCommand("synapse.chooseModel", async () => {
      if (await chooseModel()) await refreshAi();
    }),
    vscode.commands.registerCommand("synapse.showLog", showOutput),
    vscode.commands.registerCommand("synapse.openNode", (id: string) => openSymbol({ id })),
    vscode.commands.registerCommand("synapse.openGraphMap", () => {
      const r = requireGraph();
      if (!r) return;
      if (!openGraphMap(context.extensionUri, r, (id) => void openSymbol({ id }))) {
        vscode.window.showInformationMessage("Synapse: the graph map isn't generated yet. Rebuild the graph to create it.", "Build Graph")
          .then((a) => a && vscode.commands.executeCommand("synapse.buildGraph"));
      }
    })
  );

  registerLmTools(context, {
    engine,
    graphRoot: () => {
      const r = root();
      if (!r) return { unavailable: "No folder is open, so Synapse has no graph. Read files directly instead." };
      if (!vscode.workspace.isTrusted) return { unavailable: "This workspace isn't trusted, so Synapse is disabled." };
      if (!graph.getStats()) return { unavailable: "No Synapse graph exists yet. Ask the user to run 'Synapse: Build Graph', or read files directly." };
      return { root: r };
    },
    queryBudget: () => config().get<number>("queryBudget", 2000),
  });

  // --- Events ------------------------------------------------------------------------

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const r = root();
      if (!r || !config().get<boolean>("autoUpdateOnSave", true) || !vscode.workspace.isTrusted) return;
      if (doc.uri.scheme !== "file" || !isCodeFile(doc.fileName)) return;
      const rel = path.relative(r, doc.fileName);
      if (rel.startsWith("..") || path.isAbsolute(rel) || rel.split(/[\\/]/).includes("graphify-out")) return;
      scheduleUpdate();
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("synapse.ai")) void refreshAi();
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      refresh();
      void maybeAutoBuild();
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => refresh()),
    vscode.extensions.onDidChange(() => void refreshAi())
  );
  if (vscode.lm.onDidChangeChatModels) {
    context.subscriptions.push(vscode.lm.onDidChangeChatModels(() => void refreshAi()));
  }

  const r0 = root();
  if (r0) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(r0, "graphify-out/graph.json"));
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const onGraphChange = () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => {
        refresh();
        refreshGraphMap(context.extensionUri, r0, (id) => void openSymbol({ id }));
      }, 300);
    };
    watcher.onDidChange(onGraphChange);
    watcher.onDidCreate(onGraphChange);
    watcher.onDidDelete(onGraphChange);
    context.subscriptions.push(watcher);
  }

  async function maybeAutoBuild(): Promise<void> {
    const r = root();
    if (!r || !vscode.workspace.isTrusted || !config().get<boolean>("autoBuildOnOpen", true)) return;
    if (graph.getStats() || tasks.length) return;
    const found = await vscode.workspace.findFiles(new vscode.RelativePattern(r, PROJECT_GLOB), EXCLUDE_GLOB, 1);
    if (found.length) await build();
  }

  refresh();
  void refreshAi();
  void maybeAutoBuild();

  if (!context.globalState.get<boolean>("synapse.welcomed") && root()) {
    void context.globalState.update("synapse.welcomed", true);
    void vscode.commands.executeCommand(`${SidebarProvider.viewId}.focus`);
  }

  log(`Synapse ${version} activated`);

  if (context.extensionMode !== vscode.ExtensionMode.Test) return undefined;
  return {
    engine,
    state: currentState,
    setAiOverride: (handler) => {
      aiOverride = handler;
      void refreshAi();
    },
    lastView: () => lastView,
  };
}

export function deactivate(): void {
  // Engine processes are per-command and end with their task; nothing persistent to stop.
}
