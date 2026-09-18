import * as vscode from "vscode";
import type { Engine } from "./engine/engine";
import { EngineError } from "./engine/engine";
import { dedupeChecks } from "./ui/presenters";
import { log, logError } from "./logger";

// Tools GitHub Copilot's agent mode can call. They use only the local graph;
// the model calling them is the user's own Copilot session.

export interface ToolContext {
  engine: Engine;
  /** Workspace root when a graph is available, else a reason it isn't. */
  graphRoot(): { root: string } | { unavailable: string };
  queryBudget(): number;
}

const text = (s: string) => new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(s)]);

async function withGraph(
  ctx: ToolContext,
  token: vscode.CancellationToken,
  fn: (root: string, signal: AbortSignal) => Promise<string>
): Promise<vscode.LanguageModelToolResult> {
  const g = ctx.graphRoot();
  if ("unavailable" in g) return text(g.unavailable);
  const abort = new AbortController();
  const sub = token.onCancellationRequested(() => abort.abort());
  try {
    return text(await fn(g.root, abort.signal));
  } catch (err) {
    logError("language model tool failed", err);
    return text(`Synapse could not complete this request: ${err instanceof EngineError ? err.message : String(err)}`);
  } finally {
    sub.dispose();
  }
}

class SearchTool implements vscode.LanguageModelTool<{ question: string }> {
  constructor(private readonly ctx: ToolContext) {}
  prepareInvocation(options: vscode.LanguageModelToolInvocationPrepareOptions<{ question: string }>) {
    return { invocationMessage: `Searching the Synapse graph for "${options.input.question}"` };
  }
  invoke(options: vscode.LanguageModelToolInvocationOptions<{ question: string }>, token: vscode.CancellationToken) {
    return withGraph(this.ctx, token, async (root, signal) => {
      const result = await this.ctx.engine.search(root, String(options.input.question ?? ""), this.ctx.queryBudget(), signal);
      log(`tool synapse_query: ${result.nodes.length} nodes`);
      return result.raw || "No matching nodes found.";
    });
  }
}

class ExplainTool implements vscode.LanguageModelTool<{ symbol: string }> {
  constructor(private readonly ctx: ToolContext) {}
  prepareInvocation(options: vscode.LanguageModelToolInvocationPrepareOptions<{ symbol: string }>) {
    return { invocationMessage: `Looking up ${options.input.symbol} in the Synapse graph` };
  }
  invoke(options: vscode.LanguageModelToolInvocationOptions<{ symbol: string }>, token: vscode.CancellationToken) {
    return withGraph(this.ctx, token, async (root, signal) => {
      const result = await this.ctx.engine.explain(root, String(options.input.symbol ?? ""), signal);
      return result.raw || `No node named '${options.input.symbol}' found.`;
    });
  }
}

class VerifyTool implements vscode.LanguageModelTool<{ code: string }> {
  constructor(private readonly ctx: ToolContext) {}
  prepareInvocation() {
    return { invocationMessage: "Verifying code against the Synapse graph" };
  }
  invoke(options: vscode.LanguageModelToolInvocationOptions<{ code: string }>, token: vscode.CancellationToken) {
    return withGraph(this.ctx, token, async (root, signal) => {
      const report = await this.ctx.engine.verify(root, String(options.input.code ?? ""), signal);
      const checks = dedupeChecks(report.checks.results);
      const lines = [
        `Overall: ${report.verdict} (supported=${checks.filter((c) => c.verdict === "SUPPORTED").length}, ` +
          `contradicted=${checks.filter((c) => c.verdict === "CONTRADICTED").length}, ` +
          `unknown=${checks.filter((c) => c.verdict === "UNKNOWN").length}).`,
        "CONTRADICTED means the repository graph knows the owner but not this member: likely a hallucinated API; fix it before using the code.",
        "UNKNOWN means not defined in this repository (external library, built-in, or new code): not an error by itself.",
      ];
      for (const c of checks.sort((a, b) => (a.verdict === "CONTRADICTED" ? -1 : b.verdict === "CONTRADICTED" ? 1 : 0))) {
        lines.push(`- [${c.verdict}] ${c.claim}: ${c.reason}`);
      }
      for (const n of report.notes) lines.push(`- note: ${n}`);
      return lines.join("\n");
    });
  }
}

export function registerLmTools(context: vscode.ExtensionContext, ctx: ToolContext): void {
  if (!("registerTool" in vscode.lm)) {
    log("Language Model Tools API unavailable in this VS Code version; Copilot tools not registered");
    return;
  }
  context.subscriptions.push(
    vscode.lm.registerTool("synapse_query", new SearchTool(ctx)),
    vscode.lm.registerTool("synapse_explain", new ExplainTool(ctx)),
    vscode.lm.registerTool("synapse_verify_code", new VerifyTool(ctx))
  );
  log("registered Copilot agent tools: synapse_query, synapse_explain, synapse_verify_code");
}
