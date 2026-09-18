import * as fs from "fs";
import * as path from "path";
import { runProcess, tailOf } from "./proc";

// Pure Node (no `vscode` import): sets up Synapse's private Python environment
// and installs the engine wheel that ships inside the extension.

export interface EngineManifest {
  wheel: string;
  sha256: string;
  source: string;
}

export interface ProvisionOptions {
  extensionPath: string;
  /** Synapse's data directory (default ~/.synapse; kept short for Windows MAX_PATH). */
  home: string;
  pythonPath?: string;
  devPackagePath?: string;
  signal?: AbortSignal;
  onProgress?: (message: string) => void;
  log: (message: string) => void;
}

export type ProvisionFailure = "no-python" | "venv" | "install" | "engine-missing" | "cancelled";

export type ProvisionResult =
  | { ok: true; graphify: string }
  | { ok: false; kind: ProvisionFailure; message: string };

const MIN_PYTHON: [number, number] = [3, 10];
const INSTALL_TIMEOUT_MS = 15 * 60 * 1000;
const PROBE_TIMEOUT_MS = 60 * 1000;

/** Modules only the Synapse fork has; their absence means a foreign engine. */
const PROBE = [
  "import graphify, graphify.bidirectional_reasoner, graphify.preexec_validate",
  "import graphify.community_retrieval, graphify.weighted_retrieval, graphify.semantic_graph",
  "from graphify.llm import BACKENDS",
  "assert 'editor-bridge' in BACKENDS, 'engine lacks the editor-bridge backend'",
  "print(graphify.__file__)",
].join("; ");

export function venvDir(home: string): string {
  return path.join(home, "vscode-venv");
}

function venvBin(home: string, name: string): string {
  return process.platform === "win32"
    ? path.join(venvDir(home), "Scripts", `${name}.exe`)
    : path.join(venvDir(home), "bin", name);
}

export function venvPython(home: string): string {
  return venvBin(home, "python");
}

/** The engine's console script. Preferred over `python -m graphify`, which would
 * import a `graphify/` folder in the user's workspace instead of the engine. */
export function graphifyExecutable(home: string): string {
  return venvBin(home, "graphify");
}

function markerPath(home: string): string {
  return path.join(venvDir(home), "synapse-engine.json");
}

/** Environment for every engine process: UTF-8 I/O on Windows, and no
 * PYTHONPATH/PYTHONHOME that could shadow the installed engine. */
export function engineEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;
  delete env.PYTHONSTARTUP;
  return {
    ...env,
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    PIP_DISABLE_PIP_VERSION_CHECK: "1",
    PIP_NO_INPUT: "1",
    GRAPHIFY_NO_TIPS: "1",
    ...extra,
  };
}

export function readManifest(extensionPath: string): EngineManifest | null {
  try {
    const m = JSON.parse(
      fs.readFileSync(path.join(extensionPath, "engine", "manifest.json"), "utf8")
    ) as EngineManifest;
    const wheel = path.join(extensionPath, "engine", m.wheel);
    return m.wheel && m.sha256 && fs.existsSync(wheel) ? m : null;
  } catch {
    return null;
  }
}

export function parsePythonVersion(text: string): [number, number] | null {
  const m = text.match(/(\d+)\.(\d+)/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function versionOk(v: [number, number]): boolean {
  return v[0] > MIN_PYTHON[0] || (v[0] === MIN_PYTHON[0] && v[1] >= MIN_PYTHON[1]);
}

export function pythonCandidates(configured?: string): [string, string[]][] {
  if (configured) return [[configured, []]];
  return process.platform === "win32"
    ? [["py", ["-3"]], ["python", []], ["python3", []]]
    : [["python3", []], ["python", []]];
}

async function findSystemPython(
  opts: ProvisionOptions
): Promise<{ cmd: string; pre: string[]; version: string } | null> {
  for (const [cmd, pre] of pythonCandidates(opts.pythonPath)) {
    const r = await runProcess(
      cmd,
      [...pre, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
      { env: engineEnv(), signal: opts.signal, timeoutMs: PROBE_TIMEOUT_MS }
    );
    const version = r.code === 0 ? parsePythonVersion(r.stdout) : null;
    if (version && versionOk(version)) {
      opts.log(`using Python ${version.join(".")} (${[cmd, ...pre].join(" ")})`);
      return { cmd, pre, version: version.join(".") };
    }
    const why = r.spawnError ?? (r.stdout.trim() || tailOf(r.stderr, 1));
    opts.log(`skipping Python candidate ${cmd}: ${why || "not found"}`);
  }
  return null;
}

async function probe(opts: ProvisionOptions): Promise<boolean> {
  const py = venvPython(opts.home);
  if (!fs.existsSync(py) || !fs.existsSync(graphifyExecutable(opts.home))) return false;
  const r = await runProcess(py, ["-c", PROBE], {
    cwd: venvDir(opts.home),
    env: engineEnv(),
    signal: opts.signal,
    timeoutMs: PROBE_TIMEOUT_MS,
  });
  if (r.code !== 0) opts.log(`engine probe failed: ${tailOf(r.stderr) || r.spawnError}`);
  return r.code === 0;
}

async function pip(opts: ProvisionOptions, args: string[]): Promise<{ ok: boolean; detail: string }> {
  const r = await runProcess(venvPython(opts.home), ["-m", "pip", ...args], {
    cwd: venvDir(opts.home),
    env: engineEnv(),
    signal: opts.signal,
    timeoutMs: INSTALL_TIMEOUT_MS,
  });
  return { ok: r.code === 0, detail: tailOf(r.stderr) || r.spawnError || "" };
}

async function installEngine(
  opts: ProvisionOptions,
  wheel: string | null
): Promise<{ ok: boolean; detail: string }> {
  const quiet = ["install", "--quiet", "--disable-pip-version-check", "--no-input"];
  if (opts.devPackagePath) {
    return pip(opts, [...quiet, "-e", path.resolve(opts.devPackagePath)]);
  }
  // Replace whatever engine is installed (e.g. upstream graphifyy from an older
  // Synapse release) without reinstalling every dependency, then make sure the
  // dependencies themselves are satisfied.
  const replace = await pip(opts, [...quiet, "--force-reinstall", "--no-deps", wheel!]);
  if (!replace.ok) return replace;
  return pip(opts, [...quiet, wheel!]);
}

async function createVenv(opts: ProvisionOptions): Promise<ProvisionResult | null> {
  opts.onProgress?.("Finding Python 3.10+");
  const system = await findSystemPython(opts);
  if (opts.signal?.aborted) return cancelled();
  if (!system) {
    return {
      ok: false,
      kind: "no-python",
      message: opts.pythonPath
        ? `The configured synapse.pythonPath (${opts.pythonPath}) is not a Python 3.10+ interpreter.`
        : "Synapse needs Python 3.10 or newer. Install it from python.org (or set synapse.pythonPath), then try again.",
    };
  }
  opts.onProgress?.("Creating Synapse's private environment");
  fs.mkdirSync(opts.home, { recursive: true });
  const r = await runProcess(system.cmd, [...system.pre, "-m", "venv", "--clear", venvDir(opts.home)], {
    env: engineEnv(),
    signal: opts.signal,
    timeoutMs: INSTALL_TIMEOUT_MS,
  });
  if (r.cancelled) return cancelled();
  if (r.code !== 0) {
    const detail = tailOf(r.stderr) || r.spawnError || "";
    return {
      ok: false,
      kind: "venv",
      message: /ensurepip|python3-venv/i.test(detail)
        ? "Python's venv module is unavailable. Install it (e.g. `sudo apt install python3-venv`) and try again."
        : `Could not create a Python environment: ${detail}`,
    };
  }
  return null;
}

function cancelled(): ProvisionResult {
  return { ok: false, kind: "cancelled", message: "Setup was cancelled." };
}

/**
 * Make sure the private venv exists and runs exactly the engine bundled with
 * this extension (or the configured dev checkout). Idempotent and cheap when
 * already up to date: one marker read plus one import probe.
 */
export async function ensureEngine(opts: ProvisionOptions): Promise<ProvisionResult> {
  const manifest = opts.devPackagePath ? null : readManifest(opts.extensionPath);
  if (!opts.devPackagePath && !manifest) {
    return {
      ok: false,
      kind: "engine-missing",
      message: "This copy of Synapse is missing its bundled engine. Reinstall the extension.",
    };
  }
  const wanted = opts.devPackagePath ? `dev:${path.resolve(opts.devPackagePath)}` : manifest!.sha256;
  const wheel = manifest ? path.join(opts.extensionPath, "engine", manifest.wheel) : null;

  let installed: string | undefined;
  try {
    installed = JSON.parse(fs.readFileSync(markerPath(opts.home), "utf8")).engine;
  } catch {
    installed = undefined;
  }
  if (installed === wanted && (await probe(opts))) {
    return { ok: true, graphify: graphifyExecutable(opts.home) };
  }
  if (opts.signal?.aborted) return cancelled();

  // Try an in-place install first (fast upgrade path); if the venv itself is
  // missing or broken, rebuild it from scratch once.
  for (const fresh of [false, true]) {
    const pyRuns =
      !fresh &&
      fs.existsSync(venvPython(opts.home)) &&
      (await runProcess(venvPython(opts.home), ["-c", "import pip"], {
        env: engineEnv(),
        timeoutMs: PROBE_TIMEOUT_MS,
      })).code === 0;
    if (!pyRuns) {
      if (!fresh) continue;
      const failure = await createVenv(opts);
      if (failure) return failure;
    }
    opts.onProgress?.(
      fresh ? "Installing the Synapse engine (first run, about a minute)" : "Updating the Synapse engine"
    );
    const result = await installEngine(opts, wheel);
    if (opts.signal?.aborted) return cancelled();
    if (result.ok && (await probe(opts))) {
      fs.writeFileSync(markerPath(opts.home), JSON.stringify({ engine: wanted, source: manifest?.source ?? "dev" }));
      opts.log(`engine ready (${manifest ? `${manifest.wheel}, source ${manifest.source}` : wanted})`);
      return { ok: true, graphify: graphifyExecutable(opts.home) };
    }
    opts.log(`engine install ${fresh ? "failed" : "in place failed, rebuilding the environment"}: ${result.detail}`);
    if (fresh) {
      return {
        ok: false,
        kind: "install",
        message:
          "Installing the Synapse engine failed. The first run downloads dependencies from PyPI, so check your internet connection or proxy. " +
          (result.detail ? `Details: ${result.detail.split("\n").pop()}` : ""),
      };
    }
  }
  return { ok: false, kind: "install", message: "Installing the Synapse engine failed." };
}
