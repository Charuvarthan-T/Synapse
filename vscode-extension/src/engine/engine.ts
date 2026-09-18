import * as path from "path";
import { runProcess, tailOf } from "./proc";
import { engineEnv, ensureEngine, ProvisionResult } from "./provisioner";
import { parseExplainOutput, parseSearchOutput, ExplainResult, SearchResult } from "./parsers";
import { CompletionHandler, startBridge } from "../ai/bridgeServer";

// Pure Node (no `vscode` import): the extension's single entry point to the
// Synapse engine CLI. Every graph operation goes through here.

export interface EngineConfig {
  extensionPath: string;
  home: string;
  pythonPath?: string;
  devPackagePath?: string;
  log: (message: string) => void;
}

/** A failure with a short user-facing message; details go to the log. */
export class EngineError extends Error {
  constructor(message: string, readonly detail = "", readonly kind: string = "engine") {
    super(message);
  }
}

export interface Verdicted {
  verdict: "SUPPORTED" | "CONTRADICTED" | "UNKNOWN";
  claim: string;
  reason: string;
  evidence: { kind: string; detail: string; node_ids: string[] }[];
}

export interface ReasonResult {
  question: string;
  draft_answer: string;
  final_answer: string;
  revised: boolean;
  claims: { type: string; describe: string }[];
  validation: {
    verdict: string;
    results: Verdicted[];
    counts: { supported: number; contradicted: number; unknown: number };
  };
  parse_errors: string[];
  provider_errors: string[];
  graph_context_chars: number;
}

export interface VerifyResult {
  verdict: string;
  status: string;
  checks: { verdict: string; results: Verdicted[]; counts: { supported: number; contradicted: number; unknown: number } };
  notes: string[];
}

export interface EnrichResult {
  edgesAdded: number;
  edgesAugmented: number;
  conceptsAdded: number;
  requests: number;
  failedRequests: number;
  summary: string;
}

interface RunOpts {
  signal?: AbortSignal;
  input?: string;
  env?: Record<string, string>;
  onOutput?: (chunk: string) => void;
}

const COMMAND_TIMEOUT_MS = 60 * 60 * 1000;

export function graphDir(root: string): string {
  return path.join(root, "graphify-out");
}

export function graphPath(root: string): string {
  return path.join(graphDir(root), "graph.json");
}

export function graphHtmlPath(root: string): string {
  return path.join(graphDir(root), "graph.html");
}

/** Keep a free-text question from being read as a CLI flag by the engine. */
export function asArgument(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.startsWith("-") ? ` ${clean}` : clean;
}

export class Engine {
  private ready?: Promise<ProvisionResult>;
  private readyKey?: string;

  constructor(private readonly config: () => EngineConfig) {}

  /** Set up (once per session and configuration) the private engine install. */
  async ensure(signal?: AbortSignal, onProgress?: (message: string) => void): Promise<string> {
    const cfg = this.config();
    const key = `${cfg.pythonPath ?? ""}|${cfg.devPackagePath ?? ""}`;
    if (!this.ready || this.readyKey !== key) {
      this.readyKey = key;
      this.ready = ensureEngine({ ...cfg, signal, onProgress });
    }
    const result = await this.ready;
    if (!result.ok) {
      this.ready = undefined; // retry on the next call
      throw new EngineError(result.message, "", result.kind);
    }
    return result.graphify;
  }

  private async run(root: string, args: string[], opts: RunOpts = {}) {
    // Memoized per configuration, so this is cheap and picks up setting changes.
    const exe = await this.ensure(opts.signal);
    const log = this.config().log;
    log(`$ graphify ${args.map((a) => (a.length > 80 ? `${a.slice(0, 77)}...` : a)).join(" ")}`);
    const r = await runProcess(exe, args, {
      cwd: root,
      env: engineEnv(opts.env),
      input: opts.input,
      signal: opts.signal,
      timeoutMs: COMMAND_TIMEOUT_MS,
      onStdout: opts.onOutput,
      onStderr: opts.onOutput,
    });
    if (r.cancelled) throw new EngineError("Cancelled.", "", "cancelled");
    if (r.spawnError) {
      this.ready = undefined;
      throw new EngineError("The Synapse engine could not start. It will be reinstalled on the next run.", r.spawnError);
    }
    if (r.stderr.trim()) log(tailOf(r.stderr, 20));
    return r;
  }

  private fail(what: string, r: { code: number | null; stderr: string; stdout: string }): never {
    const detail = tailOf(r.stderr || r.stdout);
    throw new EngineError(`${what} failed${detail ? `: ${detail.split("\n").pop()}` : "."}`, detail);
  }

  /** Full AST rebuild (local only, no AI), then clustering + report + map. */
  async build(root: string, signal?: AbortSignal, onProgress?: (m: string) => void): Promise<void> {
    await this.ensure(signal, onProgress);
    onProgress?.("Parsing source files");
    const extract = await this.run(root, ["extract", root, "--code-only"], {
      signal,
      onOutput: (chunk) => {
        const m = chunk.match(/AST extraction on (\d+) code files/);
        if (m) onProgress?.(`Parsing ${Number(m[1]).toLocaleString()} files`);
      },
    });
    if (extract.code !== 0) {
      if (/graph is empty|found 0 code/.test(extract.stdout + extract.stderr)) {
        throw new EngineError("No supported source files were found in this workspace.", "", "empty");
      }
      this.fail("Building the graph", extract);
    }
    onProgress?.("Detecting communities");
    const cluster = await this.run(root, ["cluster-only", root, "--no-label"], { signal });
    if (cluster.code !== 0) this.fail("Clustering the graph", cluster);
  }

  /** Incremental AST refresh after edits; also regenerates the report and map. */
  async update(root: string, signal?: AbortSignal): Promise<void> {
    const r = await this.run(root, ["update", root], { signal });
    if (r.code !== 0) this.fail("Updating the graph", r);
  }

  /** Relationship-weighted, community-aware retrieval. */
  async search(root: string, question: string, budget: number, signal?: AbortSignal): Promise<SearchResult> {
    const r = await this.run(
      root,
      ["query", asArgument(question), "--graph", graphPath(root), "--budget", String(Math.round(budget)), "--community-aware"],
      { signal }
    );
    if (r.code !== 0) this.fail("Search", r);
    return parseSearchOutput(r.stdout.trim());
  }

  async explain(root: string, symbol: string, signal?: AbortSignal): Promise<ExplainResult> {
    const r = await this.run(root, ["explain", asArgument(symbol), "--graph", graphPath(root)], { signal });
    if (r.code !== 0) this.fail("Explain", r);
    return parseExplainOutput(r.stdout.trim());
  }

  /** Pre-execution hallucination check of a code snippet against the graph. */
  async verify(root: string, code: string, signal?: AbortSignal): Promise<VerifyResult> {
    const r = await this.run(root, ["preexec-check", "--graph", graphPath(root)], { signal, input: code });
    if (r.code !== 0 && r.code !== 2) this.fail("Verification", r);
    return parseJson<VerifyResult>(r.stdout, "verification");
  }

  /** Bidirectional LLM <-> graph reasoning, with the editor's AI as the LLM. */
  async reason(
    root: string,
    question: string,
    complete: CompletionHandler,
    signal?: AbortSignal,
    onRequest?: (count: number) => void
  ): Promise<ReasonResult> {
    await this.ensure(signal);
    const ai = recordErrors(complete);
    const bridge = await startBridge(ai.handler, onRequest);
    try {
      const r = await this.run(
        root,
        ["reason", asArgument(question), "--graph", graphPath(root), "--backend", "editor-bridge", "--json"],
        { signal, env: bridge.env }
      );
      if (r.code !== 0) this.fail("Reasoning", r);
      const result = parseJson<ReasonResult>(r.stdout, "reasoning");
      // The engine reports a failed first call as a placeholder draft; surface the real cause.
      if (result.provider_errors.length && !result.claims.length && ai.errors.length && !ai.successes) {
        throw new EngineError(ai.errors[ai.errors.length - 1], result.provider_errors.join("\n"), "ai");
      }
      return result;
    } finally {
      await bridge.close();
    }
  }

  /** Add LLM-inferred intent relations (handles, validates, manages, ...) to the graph. */
  async enrich(
    root: string,
    complete: CompletionHandler,
    signal?: AbortSignal,
    onRequest?: (count: number) => void
  ): Promise<EnrichResult> {
    await this.ensure(signal);
    const ai = recordErrors(complete);
    const bridge = await startBridge(ai.handler, onRequest);
    let stdout: string;
    try {
      const r = await this.run(
        root,
        ["semantic-graph", "--graph", graphPath(root), "--backend", "editor-bridge", "--batch-size", "20"],
        { signal, env: bridge.env }
      );
      if (r.code !== 0) this.fail("Enriching the graph", r);
      stdout = r.stdout;
    } finally {
      await bridge.close();
    }
    if (!ai.successes && ai.errors.length) {
      throw new EngineError(ai.errors[ai.errors.length - 1], ai.errors.join("\n"), "ai");
    }
    const m = stdout.match(/\+(\d+) edges, (\d+) edges augmented, \+(\d+) concept nodes/);
    const result: EnrichResult = {
      edgesAdded: m ? Number(m[1]) : 0,
      edgesAugmented: m ? Number(m[2]) : 0,
      conceptsAdded: m ? Number(m[3]) : 0,
      requests: ai.successes + ai.errors.length,
      failedRequests: ai.errors.length,
      summary: stdout.trim(),
    };
    if (m) {
      // Regenerate clustering, report and map so they include the new relations.
      const cluster = await this.run(root, ["cluster-only", root, "--no-label"], { signal });
      if (cluster.code !== 0) this.fail("Refreshing the graph map", cluster);
    }
    return result;
  }
}

/** Wrap an AI handler to keep the real provider errors, which the engine's
 * retry layer replaces with a generic "provider failed" message. */
function recordErrors(complete: CompletionHandler) {
  const state = {
    errors: [] as string[],
    successes: 0,
    handler: (async (req, signal) => {
      try {
        const out = await complete(req, signal);
        state.successes += 1;
        return out;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (state.errors[state.errors.length - 1] !== message) state.errors.push(message);
        throw err;
      }
    }) as CompletionHandler,
  };
  return state;
}

function parseJson<T>(stdout: string, what: string): T {
  const start = stdout.indexOf("{");
  try {
    return JSON.parse(stdout.slice(start)) as T;
  } catch {
    throw new EngineError(`The engine returned an unreadable ${what} result.`, stdout.slice(0, 2000));
  }
}
