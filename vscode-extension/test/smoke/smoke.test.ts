// Production smoke test for an *installed* .vsix: uses only public surfaces
// (commands, Copilot tools, files on disk, a fake language model standing in
// for GitHub Copilot). Test hooks are unavailable in production mode.
import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

async function waitFor<T>(what: string, fn: () => T | undefined | false, timeoutMs = 300_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

describe("Installed Synapse .vsix", function () {
  this.timeout(15 * 60 * 1000);
  const root = () => vscode.workspace.workspaceFolders![0].uri.fsPath;

  it("activates in production mode without exposing test hooks", async () => {
    const ext = vscode.extensions.getExtension("synapse-labs.synapse");
    assert.ok(ext, "installed extension not found");
    const api = await ext.activate();
    assert.strictEqual(api, undefined, "test hooks must only exist in test mode");
  });

  it("provisions the bundled engine on first use and builds the graph", async () => {
    await vscode.commands.executeCommand("synapse.buildGraph");
    const graph = path.join(root(), "graphify-out", "graph.json");
    assert.ok(fs.existsSync(graph), "graph.json was not written");
    assert.ok(fs.existsSync(path.join(root(), "graphify-out", "graph.html")));
    assert.ok(fs.readFileSync(graph, "utf8").includes("hash_password"));
  });

  it("answers Copilot agent tools from the graph", async () => {
    const token = new vscode.CancellationTokenSource().token;
    const r = await vscode.lm.invokeTool("synapse_query", { input: { question: "login" }, toolInvocationToken: undefined }, token);
    const text = r.content.map((p) => (p instanceof vscode.LanguageModelTextPart ? p.value : "")).join("");
    assert.match(text, /NODE login\(\)/);
  });

  it("runs Ask through a VS Code language model (the Copilot path)", async () => {
    const cfg = vscode.workspace.getConfiguration("synapse.ai");
    await cfg.update("provider", "copilot", vscode.ConfigurationTarget.Global);
    await cfg.update("model", "synapse-fake-model", vscode.ConfigurationTarget.Global);
    const fake = vscode.extensions.getExtension("synapse-test.synapse-fake-lm")!;
    const prompts = ((await fake.activate()) as { prompts: string[] }).prompts;
    const before = prompts.length;
    await vscode.commands.executeCommand("synapse.ask", "how does login work");
    await waitFor("the model to be asked", () => prompts.length > before, 60_000);
    assert.match(prompts[before], /GRAPH CONTEXT/);
    assert.match(prompts[before], /login\(\)/);
  });
});
