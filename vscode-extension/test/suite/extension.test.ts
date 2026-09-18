// Runs inside a real VS Code instance against a copy of
// test/fixtures/sample-workspace, with the real Synapse engine.
import * as assert from "assert";
import * as path from "path";
import * as vscode from "vscode";
import type { SynapseTestApi } from "../../src/extension";

const EXTENSION_ID = "synapse-labs.synapse";

const COMMANDS = [
  "synapse.buildGraph",
  "synapse.ask",
  "synapse.search",
  "synapse.explainSymbol",
  "synapse.verifyCode",
  "synapse.enrichGraph",
  "synapse.openGraphMap",
  "synapse.chooseModel",
  "synapse.showLog",
  "synapse.openNode",
];

async function waitFor<T>(what: string, fn: () => T | undefined | false, timeoutMs = 120_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 200));
  }
}

describe("Synapse in VS Code", function () {
  this.timeout(10 * 60 * 1000);
  let api: SynapseTestApi;
  const root = () => vscode.workspace.workspaceFolders![0].uri.fsPath;
  const view = () => api.lastView() as any;

  before(async () => {
    const ext = vscode.extensions.getExtension<SynapseTestApi>(EXTENSION_ID);
    assert.ok(ext, "extension not found");
    api = await ext.activate();
    assert.ok(api, "test API is exposed in test mode");
  });

  afterEach(async () => {
    api.setAiOverride(undefined);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  });

  it("registers its commands, sidebar view and Copilot tools", async () => {
    const all = await vscode.commands.getCommands(true);
    for (const cmd of COMMANDS) assert.ok(all.includes(cmd), `missing command ${cmd}`);
    const pkg = vscode.extensions.getExtension(EXTENSION_ID)!.packageJSON;
    assert.deepStrictEqual(pkg.contributes.views.synapse.map((v: { id: string }) => v.id), ["synapse.dashboard"]);
    const declared = pkg.contributes.languageModelTools.map((t: { name: string }) => t.name).sort();
    assert.deepStrictEqual(declared, ["synapse_explain", "synapse_query", "synapse_verify_code"]);
    const registered = vscode.lm.tools.map((t) => t.name);
    for (const name of declared) assert.ok(registered.includes(name), `tool ${name} not registered`);
  });

  it("starts without a graph when auto-build is off", () => {
    assert.strictEqual(api.state().phase, "empty");
  });

  it("builds the graph", async () => {
    await vscode.commands.executeCommand("synapse.buildGraph");
    const state = api.state();
    assert.strictEqual(state.phase, "ready", state.error ?? "");
    assert.ok(state.stats!.nodes >= 15);
    assert.ok(state.stats!.keySymbols.length > 0);
    assert.strictEqual(state.task, null);
  });

  it("searches the graph (weighted, community-aware)", async () => {
    await vscode.commands.executeCommand("synapse.search", "how does login work");
    const v = view();
    assert.strictEqual(v.kind, "search");
    assert.ok(v.nodes.some((n: { label: string }) => n.label === "login()"));
    assert.match(v.strategyShort, /Weighted/);
  });

  it("answers with bidirectional reasoning and reports claim verdicts", async () => {
    let calls = 0;
    api.setAiOverride(async () => {
      calls++;
      return {
        text: JSON.stringify({
          answer: "`login()` calls `hash_password()`.",
          claims: [{ type: "calls", source: "login", target: "hash_password" }],
        }),
      };
    });
    await vscode.commands.executeCommand("synapse.ask", "how does login work");
    const v = view();
    assert.strictEqual(v.kind, "answer", JSON.stringify(v));
    assert.strictEqual(calls, 1);
    assert.strictEqual(v.verdict.level, "ok");
    assert.ok(v.answerHtml.includes('<code class="symbol"'), "graph symbols in the answer are linked");
    assert.ok(v.claims.every((c: { verdict: string }) => c.verdict === "SUPPORTED"));
  });

  it("uses a VS Code language model (the GitHub Copilot path) when chosen", async () => {
    const cfg = vscode.workspace.getConfiguration("synapse.ai");
    await cfg.update("provider", "copilot", vscode.ConfigurationTarget.Global);
    await cfg.update("model", "synapse-fake-model", vscode.ConfigurationTarget.Global);
    try {
      await waitFor("fake model", () => api.state().ai.available && api.state().ai.label.includes("Fake Model"), 30_000);
      await vscode.commands.executeCommand("synapse.ask", "how does login work");
      const v = view();
      assert.strictEqual(v.kind, "answer", JSON.stringify(v));
      assert.match(v.providerLabel, /Fake Model/);
      assert.match(v.answerText, /hash_password/);
      const fake = vscode.extensions.getExtension("synapse-test.synapse-fake-lm")!.exports as { prompts: string[] };
      assert.ok(fake.prompts.some((p) => p.includes("GRAPH CONTEXT")), "graph context reached the model");
    } finally {
      await cfg.update("provider", undefined, vscode.ConfigurationTarget.Global);
      await cfg.update("model", undefined, vscode.ConfigurationTarget.Global);
    }
  });

  it("shows a helpful error when the chosen AI isn't available", async () => {
    const cfg = vscode.workspace.getConfiguration("synapse.ai");
    await cfg.update("provider", "codex", vscode.ConfigurationTarget.Global);
    try {
      await vscode.commands.executeCommand("synapse.ask", "how does login work");
      const v = view();
      assert.strictEqual(v.kind, "error");
      assert.match(v.message, /Codex wasn't found/);
      assert.ok(v.actions.some((a: { command: string }) => a.command === "synapse.chooseModel"));
    } finally {
      await cfg.update("provider", undefined, vscode.ConfigurationTarget.Global);
    }
  });

  it("verifies code against the graph and flags hallucinated APIs", async () => {
    const doc = await vscode.workspace.openTextDocument({
      language: "python",
      content: "from app.db import Database\n\ndb = Database()\ndb.find_user('a')\ndb.delete_everything()\n",
    });
    await vscode.window.showTextDocument(doc);
    await vscode.commands.executeCommand("synapse.verifyCode");
    const v = view();
    assert.strictEqual(v.kind, "verify");
    assert.strictEqual(v.verdict.level, "bad");
    assert.ok(v.contradicted.some((c: { text: string }) => c.text.includes("delete_everything")));
    assert.ok(v.supported.some((c: { text: string }) => c.text.includes("find_user")));
  });

  it("explains the symbol under the cursor", async () => {
    const doc = await vscode.workspace.openTextDocument(path.join(root(), "app", "auth.py"));
    const editor = await vscode.window.showTextDocument(doc);
    const offset = doc.getText().indexOf("def login") + 5;
    const pos = doc.positionAt(offset);
    editor.selection = new vscode.Selection(pos, pos);
    await vscode.commands.executeCommand("synapse.explainSymbol");
    const v = view();
    assert.strictEqual(v.kind, "explain");
    assert.strictEqual(v.node.label, "login()");
    const calls = v.groups.find((g: { title: string }) => g.title === "Calls");
    assert.ok(calls.items.some((i: { label: string }) => i.label === "hash_password()"));
  });

  it("serves Copilot agent tools from the graph", async () => {
    const token = new vscode.CancellationTokenSource().token;
    const text = async (name: string, input: object) => {
      const r = await vscode.lm.invokeTool(name, { input, toolInvocationToken: undefined }, token);
      return r.content.map((p) => (p instanceof vscode.LanguageModelTextPart ? p.value : "")).join("");
    };
    assert.match(await text("synapse_query", { question: "login" }), /NODE login\(\)/);
    assert.match(await text("synapse_explain", { symbol: "login" }), /Node: login\(\)/);
    const verdict = await text("synapse_verify_code", { code: "from app.db import Database\ndb = Database()\ndb.delete_everything()\n" });
    assert.match(verdict, /\[CONTRADICTED\] method:db\.delete_everything/);
  });

  it("opens the graph map with the bundled graph library", async () => {
    await vscode.commands.executeCommand("synapse.openGraphMap");
    const tab = await waitFor("graph map tab", () =>
      vscode.window.tabGroups.all
        .flatMap((g) => g.tabs)
        .find((t) => t.input instanceof vscode.TabInputWebview && t.input.viewType.includes("synapse.graphMap"))
    );
    assert.strictEqual(tab.label, "Synapse Graph Map");
  });

  it("jumps to a symbol's definition", async () => {
    const id = api.state().stats!.keySymbols.find((k) => k.label === "login()")?.id;
    assert.ok(id);
    await vscode.commands.executeCommand("synapse.openNode", id);
    const editor = await waitFor("editor", () => vscode.window.activeTextEditor);
    assert.ok(editor.document.fileName.endsWith(path.join("app", "auth.py")));
    assert.strictEqual(editor.selection.active.line, 14);
  });

  it("refreshes the graph incrementally after a save", async () => {
    await vscode.workspace.getConfiguration("synapse").update("autoUpdateOnSave", true, vscode.ConfigurationTarget.Workspace);
    try {
      const doc = await vscode.workspace.openTextDocument(path.join(root(), "app", "db.py"));
      const editor = await vscode.window.showTextDocument(doc);
      await editor.edit((e) => e.insert(doc.lineAt(doc.lineCount - 1).range.end, "\n\n    def remove_user(self, name):\n        self.users.pop(name, None)\n"));
      await doc.save();
      await waitFor("updated graph", () => api.state().stats!.keySymbols && api.state().stats!.nodes > 0 &&
        (require("fs").readFileSync(path.join(root(), "graphify-out", "graph.json"), "utf8") as string).includes("remove_user"), 60_000);
    } finally {
      await vscode.workspace.getConfiguration("synapse").update("autoUpdateOnSave", false, vscode.ConfigurationTarget.Workspace);
    }
  });
});
