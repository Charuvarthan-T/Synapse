import * as vscode from "vscode";
import { CompletionHandler, FatalProviderError } from "./bridgeServer";
import { completeWithClaude, completeWithCodex, discoverClaude, discoverCodex } from "./cliProviders";
import { log } from "../logger";

// Chooses which AI answers the engine's LLM calls. Synapse never asks for an
// API key: GitHub Copilot is reached through VS Code's Language Model API, and
// Claude Code / Codex through their own CLIs, all using the user's sign-in.

export type ProviderId = "copilot" | "claude-code" | "codex";
export type ProviderSetting = ProviderId | "auto";

export interface ResolvedProvider {
  id: ProviderId;
  /** e.g. "GitHub Copilot · GPT-5 mini" */
  label: string;
  complete: CompletionHandler;
}

export const CLAUDE_EXTENSION_ID = "anthropic.claude-code";
export const CODEX_EXTENSION_ID = "openai.chatgpt";

const JUSTIFICATION =
  "Synapse sends questions about this workspace, together with context from its local code graph, " +
  "to answer them and to check the answer against the graph.";

/** Copilot models that are fast and don't use premium requests, best first. */
const PREFERRED_COPILOT_FAMILIES = ["gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-5"];

function settings() {
  const cfg = vscode.workspace.getConfiguration("synapse.ai");
  return {
    provider: cfg.get<ProviderSetting>("provider", "auto"),
    model: cfg.get<string>("model", "").trim(),
  };
}

export function claudeBinary(): string | undefined {
  return discoverClaude(vscode.extensions.getExtension(CLAUDE_EXTENSION_ID)?.extensionPath);
}

export function codexBinary(): string | undefined {
  return discoverCodex(vscode.extensions.getExtension(CODEX_EXTENSION_ID)?.extensionPath);
}

function vendorName(vendor: string): string {
  return vendor === "copilot" ? "GitHub Copilot" : vendor.charAt(0).toUpperCase() + vendor.slice(1);
}

async function languageModels(): Promise<vscode.LanguageModelChat[]> {
  try {
    return await vscode.lm.selectChatModels();
  } catch (err) {
    log(`listing language models failed: ${err instanceof Error ? err.message : String(err)}`);
    return [];
  }
}

export function pickDefaultModel(models: readonly vscode.LanguageModelChat[]): vscode.LanguageModelChat | undefined {
  const copilot = models.filter((m) => m.vendor === "copilot");
  const pool = copilot.length ? copilot : [...models];
  for (const family of PREFERRED_COPILOT_FAMILIES) {
    const hit = pool.find((m) => m.family === family || m.id === family);
    if (hit) return hit;
  }
  return pool[0];
}

function lmHandler(model: vscode.LanguageModelChat): CompletionHandler {
  return async (req, signal) => {
    const cts = new vscode.CancellationTokenSource();
    const onAbort = () => cts.cancel();
    signal.addEventListener("abort", onAbort, { once: true });
    try {
      const response = await model.sendRequest(
        [vscode.LanguageModelChatMessage.User(req.prompt)],
        { justification: JUSTIFICATION },
        cts.token
      );
      let text = "";
      for await (const part of response.text) text += part;
      return { text };
    } catch (err) {
      if (err instanceof vscode.LanguageModelError) {
        if (err.code === vscode.LanguageModelError.NoPermissions.name) {
          throw new FatalProviderError(
            `Synapse doesn't have permission to use ${model.name}. Run "Synapse: Choose AI Model" and allow access when asked.`
          );
        }
        if (err.code === vscode.LanguageModelError.Blocked.name) {
          throw new FatalProviderError(`${vendorName(model.vendor)} blocked the request (quota or policy): ${err.message}`);
        }
        if (err.code === vscode.LanguageModelError.NotFound.name) {
          throw new FatalProviderError(`The model ${model.name} is no longer available. Choose another with "Synapse: Choose AI Model".`);
        }
      }
      throw err instanceof Error ? err : new Error(String(err));
    } finally {
      signal.removeEventListener("abort", onAbort);
      cts.dispose();
    }
  };
}

async function resolveCopilot(model: string): Promise<ResolvedProvider | undefined> {
  const models = await languageModels();
  const chosen = (model && models.find((m) => m.id === model)) || pickDefaultModel(models);
  if (!chosen) return undefined;
  return { id: "copilot", label: `${vendorName(chosen.vendor)} · ${chosen.name}`, complete: lmHandler(chosen) };
}

function resolveClaude(model: string): ResolvedProvider | undefined {
  const binary = claudeBinary();
  if (!binary) return undefined;
  return {
    id: "claude-code",
    label: `Claude Code${model ? ` · ${model}` : ""}`,
    complete: (req, signal) => completeWithClaude(binary, req, model || undefined, signal),
  };
}

function resolveCodex(model: string): ResolvedProvider | undefined {
  const binary = codexBinary();
  if (!binary) return undefined;
  return {
    id: "codex",
    label: `Codex${model ? ` · ${model}` : ""}`,
    complete: (req, signal) => completeWithCodex(binary, req, model || undefined, signal),
  };
}

const NOT_FOUND: Record<ProviderId, string> = {
  copilot: "GitHub Copilot isn't available. Sign in to GitHub Copilot in VS Code, or choose another AI.",
  "claude-code": "Claude Code wasn't found. Install the Claude Code extension (or its CLI) and sign in once.",
  codex: "Codex wasn't found. Install the Codex extension (or its CLI) and sign in once.",
};

/** The AI to use right now, following the user's setting. Throws a
 * FatalProviderError with guidance when nothing usable is available. */
export async function resolveProvider(): Promise<ResolvedProvider> {
  const { provider, model } = settings();
  if (provider !== "auto") {
    const resolved =
      provider === "copilot" ? await resolveCopilot(model)
      : provider === "claude-code" ? resolveClaude(model)
      : resolveCodex(model);
    if (!resolved) throw new FatalProviderError(NOT_FOUND[provider]);
    return resolved;
  }
  const resolved = (await resolveCopilot("")) ?? resolveClaude("") ?? resolveCodex("");
  if (!resolved) {
    throw new FatalProviderError(
      "No AI assistant found. Sign in to GitHub Copilot, or install Claude Code or Codex. Synapse uses your existing sign-in; no API key is needed."
    );
  }
  return resolved;
}

/** Cheap, non-prompting description of the current choice for the UI. */
export async function describeProvider(): Promise<{ label: string; available: boolean }> {
  try {
    const p = await resolveProvider();
    return { label: p.label, available: true };
  } catch {
    const { provider } = settings();
    return { label: provider === "auto" ? "No AI assistant found" : `${providerName(provider)} not found`, available: false };
  }
}

function providerName(id: ProviderId): string {
  return id === "copilot" ? "GitHub Copilot" : id === "claude-code" ? "Claude Code" : "Codex";
}

interface ModelPick extends vscode.QuickPickItem {
  provider?: ProviderSetting;
  model?: string;
  install?: string;
}

/** Quick pick over every assistant and model available to Synapse. */
export async function chooseModel(): Promise<boolean> {
  const current = settings();
  const models = await languageModels();
  const items: ModelPick[] = [
    {
      label: "$(sparkle) Auto",
      description: current.provider === "auto" ? "current" : undefined,
      detail: "Use GitHub Copilot if signed in, otherwise Claude Code, otherwise Codex.",
      provider: "auto",
      model: "",
    },
  ];

  items.push({ label: "GitHub Copilot", kind: vscode.QuickPickItemKind.Separator });
  if (models.length) {
    for (const m of models) {
      items.push({
        label: m.name,
        description: [m.vendor !== "copilot" ? vendorName(m.vendor) : "", current.provider === "copilot" && current.model === m.id ? "current" : ""]
          .filter(Boolean)
          .join(" · ") || undefined,
        detail: `${m.family} · ${m.maxInputTokens.toLocaleString()} token context`,
        provider: "copilot",
        model: m.id,
      });
    }
  } else {
    items.push({ label: "$(circle-slash) Not signed in", detail: "Sign in to GitHub Copilot in VS Code to use its models." });
  }

  const cli = (id: ProviderId, name: string, binary: string | undefined, extensionId: string, aliases: string[]) => {
    items.push({ label: name, kind: vscode.QuickPickItemKind.Separator });
    if (!binary) {
      items.push({ label: `$(extensions) Install ${name}`, detail: `${name} isn't installed. Select to open it in the Marketplace.`, install: extensionId });
      return;
    }
    for (const alias of ["", ...aliases]) {
      const isCurrent = current.provider === id && current.model === alias;
      items.push({
        label: alias ? `${name} · ${alias}` : `${name} · default model`,
        description: isCurrent ? "current" : undefined,
        detail: alias ? undefined : `Uses your ${name} sign-in (${binary})`,
        provider: id,
        model: alias,
      });
    }
  };
  cli("claude-code", "Claude Code", claudeBinary(), CLAUDE_EXTENSION_ID, ["sonnet", "opus", "haiku"]);
  cli("codex", "Codex", codexBinary(), CODEX_EXTENSION_ID, []);

  const pick = await vscode.window.showQuickPick(items, {
    title: "Synapse: Choose the AI used for Ask and Enrich",
    placeHolder: "Synapse uses your existing sign-in. No API key needed.",
    matchOnDetail: true,
  });
  if (!pick) return false;
  if (pick.install) {
    await vscode.commands.executeCommand("workbench.extensions.search", `@id:${pick.install}`);
    return false;
  }
  if (!pick.provider) return false;

  const cfg = vscode.workspace.getConfiguration("synapse.ai");
  await cfg.update("provider", pick.provider, vscode.ConfigurationTarget.Global);
  await cfg.update("model", pick.model ?? "", vscode.ConfigurationTarget.Global);
  return true;
}
