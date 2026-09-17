import * as assert from "assert";
import * as vscode from "vscode";

const EXPECTED_COMMANDS = [
  "graphify.rebuildGraph",
  "graphify.updateGraph",
  "graphify.query",
  "graphify.explainAtCursor",
  "graphify.openGraphView",
  "graphify.showOutput",
  "graphify.revealNode",
  "graphify.openGettingStarted",
];

describe("Graphify extension", () => {
  it("activates without throwing", async () => {
    const ext = vscode.extensions.getExtension("graphify-labs.graphify-vscode");
    assert.ok(ext, "extension not found — check the publisher.name in package.json");
    await ext!.activate();
    assert.strictEqual(ext!.isActive, true);
  });

  it("registers all v1 commands", async () => {
    const all = await vscode.commands.getCommands(true);
    for (const cmd of EXPECTED_COMMANDS) {
      assert.ok(all.includes(cmd), `expected command '${cmd}' to be registered`);
    }
  });

  it("contributes the graphifyExplorer tree view without error", () => {
    // Registration itself is exercised by activation above; this just
    // guards against the view id being renamed in package.json without
    // updating extension.ts (or vice versa).
    const pkg = vscode.extensions.getExtension("graphify-labs.graphify-vscode")!.packageJSON;
    const viewIds = (pkg.contributes.views.graphify as { id: string }[]).map((v) => v.id);
    assert.deepStrictEqual(viewIds, ["graphifyExplorer"]);
  });

  it("declares languageModelTools matching the tools registered at runtime", () => {
    const pkg = vscode.extensions.getExtension("graphify-labs.graphify-vscode")!.packageJSON;
    const names = (pkg.contributes.languageModelTools as { name: string }[]).map((t) => t.name);
    assert.deepStrictEqual(names.sort(), ["graphify_explain", "graphify_query"]);
  });
});
