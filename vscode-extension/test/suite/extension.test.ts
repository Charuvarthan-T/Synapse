import * as assert from "assert";
import * as vscode from "vscode";

const EXTENSION_ID = "synapse-labs.synapse";

const EXPECTED_COMMANDS = [
  "synapse.openDashboard",
  "synapse.rebuildGraph",
  "synapse.updateGraph",
  "synapse.query",
  "synapse.explainAtCursor",
  "synapse.openGraphView",
  "synapse.showOutput",
  "synapse.revealNode",
  "synapse.openGettingStarted",
];

describe("Synapse extension", () => {
  it("activates without throwing", async () => {
    const ext = vscode.extensions.getExtension(EXTENSION_ID);
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

  it("contributes the Dashboard webview view and the Knowledge Graph tree view", () => {
    // Registration itself is exercised by activation above; this just
    // guards against a view id being renamed in package.json without
    // updating extension.ts (or vice versa).
    const pkg = vscode.extensions.getExtension(EXTENSION_ID)!.packageJSON;
    const viewIds = (pkg.contributes.views.synapse as { id: string }[]).map((v) => v.id);
    assert.deepStrictEqual(viewIds, ["synapseDashboardView", "synapseExplorer"]);
  });

  it("declares languageModelTools matching the tools registered at runtime", () => {
    const pkg = vscode.extensions.getExtension(EXTENSION_ID)!.packageJSON;
    const names = (pkg.contributes.languageModelTools as { name: string }[]).map((t) => t.name);
    assert.deepStrictEqual(names.sort(), ["synapse_explain", "synapse_query"]);
  });
});
