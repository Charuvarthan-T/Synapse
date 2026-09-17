import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { log, logError } from "./logger";

export interface CliResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

const MIN_PYTHON_MINOR = 10;

/** Short, fixed cache dir (not VS Code's deeply-nested global storage path) to
 * avoid Windows MAX_PATH (260 char) failures we hit firsthand building this
 * project's own graph-extraction eval harness — long venv/site-packages paths
 * silently fail to write cache files on Windows. */
function cacheRoot(): string {
  return path.join(os.homedir(), ".graphify", "vscode-venv");
}

function venvPython(): string {
  const root = cacheRoot();
  return process.platform === "win32"
    ? path.join(root, "Scripts", "python.exe")
    : path.join(root, "bin", "python");
}

function run(cmd: string, args: string[], cwd?: string): Promise<CliResult> {
  return new Promise((resolve) => {
    log(`$ ${cmd} ${args.join(" ")}`);
    const child = cp.spawn(cmd, args, {
      cwd,
      windowsHide: true,
      env: { ...process.env },
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr?.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("error", (err) => {
      logError(`failed to spawn ${cmd}`, err);
      resolve({ code: -1, stdout, stderr: String(err) });
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function parsePythonVersion(versionOutput: string): [number, number] | null {
  const m = versionOutput.match(/Python (\d+)\.(\d+)/);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10)];
}

/** Find a system Python 3.10+, respecting the graphify.pythonPath setting. */
export async function findSystemPython(): Promise<string | null> {
  const configured = vscode.workspace
    .getConfiguration("graphify")
    .get<string>("pythonPath", "")
    ?.trim();

  const candidates: [string, string[]][] = configured
    ? [[configured, ["--version"]]]
    : [
        ["python3", ["--version"]],
        ["python", ["--version"]],
        ["py", ["-3", "--version"]],
      ];

  for (const [cmd, args] of candidates) {
    const result = await run(cmd, args);
    // Python <=3.7 prints version to stderr, not stdout.
    const combined = `${result.stdout}${result.stderr}`;
    const version = parsePythonVersion(combined);
    if (result.code === 0 && version && version[0] === 3 && version[1] >= MIN_PYTHON_MINOR) {
      log(`found system Python: ${cmd} (${combined.trim()})`);
      return cmd === "py" ? "py" : cmd;
    }
  }
  return null;
}

export type ProvisionStatus =
  | { ok: true }
  | { ok: false; reason: "no-python" | "install-failed"; detail: string };

/** Ensure a working graphify install exists in our private venv. Idempotent:
 * safe to call on every activation, does nothing if already provisioned. */
export async function ensureProvisioned(
  onProgress?: (message: string) => void
): Promise<ProvisionStatus> {
  const py = venvPython();
  if (fs.existsSync(py)) {
    const check = await run(py, ["-m", "graphify", "--help"]);
    if (check.code === 0) {
      return { ok: true };
    }
    log("existing venv is broken, reprovisioning");
  }

  onProgress?.("Looking for Python...");
  const systemPython = await findSystemPython();
  if (!systemPython) {
    return {
      ok: false,
      reason: "no-python",
      detail:
        "No Python 3.10+ found on PATH. Install Python from python.org, or set graphify.pythonPath.",
    };
  }

  const root = cacheRoot();
  fs.mkdirSync(root, { recursive: true });

  onProgress?.("Creating isolated environment...");
  const venvArgs = systemPython === "py" ? ["-3", "-m", "venv", root] : ["-m", "venv", root];
  const venvResult = await run(systemPython, venvArgs);
  if (venvResult.code !== 0) {
    return {
      ok: false,
      reason: "install-failed",
      detail: `Failed to create virtual environment: ${venvResult.stderr.slice(0, 500)}`,
    };
  }

  onProgress?.("Installing graphify (one-time, local only)...");
  const devPath = vscode.workspace
    .getConfiguration("graphify")
    .get<string>("devPackagePath", "")
    ?.trim();
  const pipTarget = devPath ? ["-e", devPath] : ["graphifyy"];
  const pipResult = await run(py, ["-m", "pip", "install", "--quiet", ...pipTarget]);
  if (pipResult.code !== 0) {
    return {
      ok: false,
      reason: "install-failed",
      detail: `pip install failed: ${pipResult.stderr.slice(0, 500)}`,
    };
  }

  const verify = await run(py, ["-m", "graphify", "--help"]);
  if (verify.code !== 0) {
    return {
      ok: false,
      reason: "install-failed",
      detail: `graphify did not install correctly: ${verify.stderr.slice(0, 500)}`,
    };
  }

  return { ok: true };
}

function graphOutDir(workspaceRoot: string): string {
  return path.join(workspaceRoot, "graphify-out");
}

export function graphJsonPath(workspaceRoot: string): string {
  return path.join(graphOutDir(workspaceRoot), "graph.json");
}

export function graphHtmlPath(workspaceRoot: string): string {
  return path.join(graphOutDir(workspaceRoot), "graph.html");
}

export function hasGraph(workspaceRoot: string): boolean {
  return fs.existsSync(graphJsonPath(workspaceRoot));
}

async function runGraphify(args: string[], cwd: string): Promise<CliResult> {
  return run(venvPython(), ["-m", "graphify", ...args], cwd);
}

/** `extract`/`update` alone only (re)write graph.json — they do NOT generate
 * graph.html or GRAPH_REPORT.md. Only `cluster-only` does. --no-label keeps
 * this LLM-free (community names fall back to their hub node's name instead
 * of an LLM-generated description) so the "no API key required" guarantee
 * holds for the visualization too. */
async function refreshVisualization(workspaceRoot: string): Promise<CliResult> {
  return runGraphify(["cluster-only", workspaceRoot, "--no-label"], workspaceRoot);
}

/** Full extraction. Always --code-only: zero LLM calls, zero API key needed —
 * this is the core "no key required" guarantee of the extension. */
export async function extract(workspaceRoot: string): Promise<CliResult> {
  const result = await runGraphify(["extract", workspaceRoot, "--code-only"], workspaceRoot);
  if (result.code !== 0) return result;
  return refreshVisualization(workspaceRoot);
}

/** Incremental, AST-only re-extraction after file edits. No LLM cost. */
export async function update(workspaceRoot: string): Promise<CliResult> {
  const result = await runGraphify(["update", workspaceRoot], workspaceRoot);
  if (result.code !== 0) return result;
  return refreshVisualization(workspaceRoot);
}

export async function query(
  workspaceRoot: string,
  question: string,
  budget: number
): Promise<CliResult> {
  return runGraphify(
    ["query", question, "--graph", graphJsonPath(workspaceRoot), "--budget", String(budget)],
    workspaceRoot
  );
}

export async function explain(workspaceRoot: string, symbol: string): Promise<CliResult> {
  return runGraphify(
    ["explain", symbol, "--graph", graphJsonPath(workspaceRoot)],
    workspaceRoot
  );
}
