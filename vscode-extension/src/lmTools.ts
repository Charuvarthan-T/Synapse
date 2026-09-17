import * as vscode from "vscode";
import * as cliService from "./cliService";
import { primaryWorkspaceRoot, isWorkspaceTrusted } from "./workspaceUtils";
import { log, logError } from "./logger";

interface QueryInput {
  question: string;
}

interface ExplainInput {
  symbol: string;
}

function noGraphResult(): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(
      "No Synapse knowledge graph is available for this workspace yet. " +
        "Ask the user to run the 'Synapse: Rebuild Graph' command, or fall back to reading files directly."
    ),
  ]);
}

function untrustedResult(): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(
      "This workspace is not trusted, so Synapse cannot run. Ask the user to trust the workspace."
    ),
  ]);
}

class QueryTool implements vscode.LanguageModelTool<QueryInput> {
  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<QueryInput>,
    _token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    if (!isWorkspaceTrusted()) return untrustedResult();
    const root = primaryWorkspaceRoot();
    if (!root || !cliService.hasGraph(root)) return noGraphResult();

    const budget = vscode.workspace.getConfiguration("synapse").get<number>("queryBudget", 2000);
    try {
      const result = await cliService.query(root, options.input.question, budget);
      if (result.code !== 0) {
        logError("synapse query (graphify engine) failed", result.stderr);
        return new vscode.LanguageModelToolResult([
          new vscode.LanguageModelTextPart(`Synapse query failed: ${result.stderr.slice(0, 300)}`),
        ]);
      }
      log(`lm tool query: "${options.input.question}" -> ${result.stdout.length} chars`);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(result.stdout || "No relevant nodes found."),
      ]);
    } catch (err) {
      logError("synapse_query tool crashed", err);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart("Synapse query encountered an internal error."),
      ]);
    }
  }
}

class ExplainTool implements vscode.LanguageModelTool<ExplainInput> {
  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<ExplainInput>,
    _token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    if (!isWorkspaceTrusted()) return untrustedResult();
    const root = primaryWorkspaceRoot();
    if (!root || !cliService.hasGraph(root)) return noGraphResult();

    try {
      const result = await cliService.explain(root, options.input.symbol);
      if (result.code !== 0) {
        logError("synapse explain (graphify engine) failed", result.stderr);
        return new vscode.LanguageModelToolResult([
          new vscode.LanguageModelTextPart(`Synapse explain failed: ${result.stderr.slice(0, 300)}`),
        ]);
      }
      log(`lm tool explain: "${options.input.symbol}"`);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(result.stdout || `No node named '${options.input.symbol}' found.`),
      ]);
    } catch (err) {
      logError("synapse_explain tool crashed", err);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart("Synapse explain encountered an internal error."),
      ]);
    }
  }
}

export function registerLmTools(context: vscode.ExtensionContext): void {
  // Guard for older VS Code builds without the Language Model Tools API.
  if (!("registerTool" in vscode.lm)) {
    log("vscode.lm.registerTool not available in this VS Code version; skipping tool registration");
    return;
  }
  context.subscriptions.push(vscode.lm.registerTool("synapse_query", new QueryTool()));
  context.subscriptions.push(vscode.lm.registerTool("synapse_explain", new ExplainTool()));
  log("registered synapse_query and synapse_explain as Language Model Tools");
}
