import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { runProcess, tailOf, ProcResult } from "../engine/proc";
import { CompletionRequest, CompletionResult, FatalProviderError } from "./bridgeServer";

// Pure Node (no `vscode` import): run the Claude Code and Codex CLIs in
// non-interactive mode. Both authenticate with the user's own subscription
// (claude.ai / ChatGPT sign-in), so Synapse never handles an API key.

const CALL_TIMEOUT_MS = 5 * 60 * 1000;
const SAFE_MODEL = /^[\w.:\-\[\]\/]+$/;

/** Fixed instructions; plain characters only (they may pass through cmd.exe). */
const SYSTEM_PROMPT =
  "You are a precise software analysis assistant. Answer only from the context in the prompt. " +
  "Follow the requested output format exactly and do not add commentary.";

function exeNames(base: string): string[] {
  return process.platform === "win32" ? [`${base}.exe`, `${base}.cmd`, base] : [base];
}

/** First existing file named `base` on PATH (Windows: .exe preferred over .cmd). */
export function findOnPath(base: string, envPath = process.env.PATH ?? process.env.Path ?? ""): string | undefined {
  for (const dir of envPath.split(path.delimiter).filter(Boolean)) {
    for (const name of exeNames(base)) {
      const full = path.join(dir.replace(/^"|"$/g, ""), name);
      try {
        if (fs.statSync(full).isFile()) return full;
      } catch {
        // not here
      }
    }
  }
  return undefined;
}

function firstExisting(paths: (string | undefined)[]): string | undefined {
  return paths.find((p) => {
    try {
      return !!p && fs.statSync(p).isFile();
    } catch {
      return false;
    }
  });
}

/** Search a directory tree (bounded depth) for an executable named `base`. */
function findBelow(dir: string, base: string, depth = 4): string | undefined {
  if (depth < 0) return undefined;
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return undefined;
  }
  const names = new Set(exeNames(base).filter((n) => n !== `${base}.cmd`));
  for (const e of entries) if (e.isFile() && names.has(e.name)) return path.join(dir, e.name);
  for (const e of entries) {
    if (e.isDirectory() && e.name !== "node_modules") {
      const hit = findBelow(path.join(dir, e.name), base, depth - 1);
      if (hit) return hit;
    }
  }
  return undefined;
}

/** Claude Code CLI: the binary bundled with the Claude Code extension, else the
 * native installer location, else PATH. */
export function discoverClaude(extensionPath?: string): string | undefined {
  const exe = process.platform === "win32" ? "claude.exe" : "claude";
  return firstExisting([
    extensionPath && path.join(extensionPath, "resources", "native-binary", exe),
    path.join(os.homedir(), ".local", "bin", exe),
    path.join(os.homedir(), ".claude", "local", exe),
  ]) ?? findOnPath("claude");
}

/** Codex CLI: the binary bundled with the Codex extension, else PATH. */
export function discoverCodex(extensionPath?: string): string | undefined {
  return (extensionPath && findBelow(path.join(extensionPath, "bin"), "codex")) || findOnPath("codex");
}

function quoteForCmd(arg: string): string {
  if (/["%^&|<>!\r\n]/.test(arg)) throw new Error(`unsafe argument for cmd.exe: ${arg}`);
  return `"${arg}"`;
}

/** Spawn a CLI. npm installs `.cmd` shims on Windows, which Node can only run
 * through cmd.exe; arguments are fixed strings validated against cmd syntax. */
function runCli(file: string, args: string[], input: string, cwd: string, signal: AbortSignal): Promise<ProcResult> {
  const opts = { cwd, input, signal, timeoutMs: CALL_TIMEOUT_MS };
  if (process.platform === "win32" && /\.(cmd|bat)$/i.test(file)) {
    const line = [file, ...args].map(quoteForCmd).join(" ");
    return runProcess(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", `"${line}"`], {
      ...opts,
      verbatim: true,
    });
  }
  return runProcess(file, args, opts);
}

function checkModel(model: string | undefined): string | undefined {
  if (model && !SAFE_MODEL.test(model)) throw new FatalProviderError(`Invalid model name: ${model}`);
  return model || undefined;
}

function failure(tool: string, r: ProcResult): Error {
  if (r.cancelled) return new Error(`${tool} request was cancelled or timed out.`);
  if (r.spawnError) return new FatalProviderError(`Could not start ${tool}: ${r.spawnError}`);
  const detail = tailOf(r.stderr || r.stdout, 4);
  if (/not logged in|login|authenticat|unauthori[sz]ed|api key/i.test(detail)) {
    return new FatalProviderError(`${tool} is not signed in. Run it once in a terminal to sign in. (${detail})`);
  }
  return new Error(`${tool} exited with code ${r.code}: ${detail}`);
}

/** Parse `claude -p --output-format json` (an object, or an event array ending in a result). */
export function parseClaudeEnvelope(stdout: string): CompletionResult {
  let data: unknown;
  try {
    data = JSON.parse(stdout);
  } catch {
    throw new Error(`Claude Code returned unreadable output: ${stdout.slice(0, 200)}`);
  }
  const envelope = (Array.isArray(data)
    ? [...data].reverse().find((e) => e && typeof e === "object" && "result" in e)
    : data) as { result?: unknown; is_error?: boolean; usage?: Record<string, number> } | undefined;
  if (!envelope || typeof envelope.result !== "string") {
    throw new Error("Claude Code returned no result.");
  }
  if (envelope.is_error) {
    const msg = envelope.result;
    if (/login|authenticat|credit|subscription|api key/i.test(msg)) throw new FatalProviderError(`Claude Code: ${msg}`);
    throw new Error(`Claude Code: ${msg}`);
  }
  const u = envelope.usage ?? {};
  return {
    text: envelope.result,
    usage: {
      input_tokens: (u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0),
      output_tokens: u.output_tokens ?? 0,
    },
  };
}

export async function completeWithClaude(
  binary: string,
  req: CompletionRequest,
  model: string | undefined,
  signal: AbortSignal
): Promise<CompletionResult> {
  const chosen = checkModel(req.model ?? model);
  const modelArgs = chosen ? ["--model", chosen] : [];
  // Lean, tool-free call: no tools, no MCP servers, a minimal system prompt,
  // and a neutral working directory so no project CLAUDE.md is loaded.
  const lean = [
    "-p", "--output-format", "json", "--no-session-persistence",
    "--tools", "", "--strict-mcp-config", "--system-prompt", SYSTEM_PROMPT, ...modelArgs,
  ];
  let r = await runCli(binary, lean, req.prompt, os.tmpdir(), signal);
  if (r.code !== 0 && !r.cancelled && /unknown option|unexpected argument/i.test(r.stderr)) {
    // Older Claude Code: fall back to the flags every version supports.
    r = await runCli(binary, ["-p", "--output-format", "json", ...modelArgs], req.prompt, os.tmpdir(), signal);
  }
  if (r.code !== 0) throw failure("Claude Code", r);
  return parseClaudeEnvelope(r.stdout);
}

export async function completeWithCodex(
  binary: string,
  req: CompletionRequest,
  model: string | undefined,
  signal: AbortSignal
): Promise<CompletionResult> {
  const chosen = checkModel(req.model ?? model);
  // An empty scratch directory: Codex is an agent, and this keeps it away from
  // the user's files. The read-only sandbox prevents any writes.
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-codex-"));
  const outFile = path.join(scratch, "answer.txt");
  const prompt =
    `${SYSTEM_PROMPT} Do not run commands or read files; everything you need is below.\n\n${req.prompt}`;
  const base = ["exec", "--skip-git-repo-check", "--color", "never", "--output-last-message", outFile];
  const modelArgs = chosen ? ["--model", chosen] : [];
  try {
    let r = await runCli(binary, [...base, "--ephemeral", "--sandbox", "read-only", ...modelArgs, "-"], prompt, scratch, signal);
    if (r.code !== 0 && !r.cancelled && /unexpected argument|unrecognized|unknown option/i.test(r.stderr)) {
      // Older Codex without --ephemeral/--sandbox on exec.
      r = await runCli(binary, [...base, ...modelArgs, "-"], prompt, scratch, signal);
    }
    if (r.code !== 0) throw failure("Codex", r);
    let text = "";
    try {
      text = fs.readFileSync(outFile, "utf8");
    } catch {
      text = r.stdout;
    }
    if (!text.trim()) throw new Error("Codex returned an empty answer.");
    return { text };
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}
