import * as cp from "child_process";

// Pure Node (no `vscode` import) so it is unit-testable without an editor.

export interface ProcResult {
  code: number | null;
  stdout: string;
  stderr: string;
  /** True when the run was stopped by the abort signal or the timeout. */
  cancelled: boolean;
  /** Set when the process could not be started at all (e.g. ENOENT). */
  spawnError?: string;
}

export interface ProcOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  input?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Receive output chunks as they arrive (progress reporting). */
  onStdout?: (chunk: string) => void;
  onStderr?: (chunk: string) => void;
  /** Windows only: pass `args` to the process exactly as given (cmd.exe /c lines). */
  verbatim?: boolean;
}

const MAX_CAPTURE = 32 * 1024 * 1024; // never buffer unbounded output

export function runProcess(cmd: string, args: string[], opts: ProcOptions = {}): Promise<ProcResult> {
  return new Promise((resolve) => {
    if (opts.signal?.aborted) {
      resolve({ code: null, stdout: "", stderr: "", cancelled: true });
      return;
    }
    let child: cp.ChildProcess;
    try {
      child = cp.spawn(cmd, args, {
        cwd: opts.cwd,
        env: opts.env ?? process.env,
        windowsHide: true,
        windowsVerbatimArguments: opts.verbatim,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      resolve({ code: null, stdout: "", stderr: "", cancelled: false, spawnError: String(err) });
      return;
    }

    let stdout = "";
    let stderr = "";
    let cancelled = false;
    let settled = false;
    const kill = () => {
      if (child.exitCode === null && !child.killed) {
        cancelled = true;
        child.kill();
      }
    };
    const timer = opts.timeoutMs ? setTimeout(kill, opts.timeoutMs) : undefined;
    opts.signal?.addEventListener("abort", kill, { once: true });

    child.stdout!.setEncoding("utf8");
    child.stderr!.setEncoding("utf8");
    child.stdout!.on("data", (d: string) => {
      if (stdout.length < MAX_CAPTURE) stdout += d;
      opts.onStdout?.(d);
    });
    child.stderr!.on("data", (d: string) => {
      if (stderr.length < MAX_CAPTURE) stderr += d;
      opts.onStderr?.(d);
    });

    const finish = (result: ProcResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener("abort", kill);
      resolve(result);
    };
    child.on("error", (err) =>
      finish({ code: null, stdout, stderr, cancelled, spawnError: err.message })
    );
    child.on("close", (code) => finish({ code, stdout, stderr, cancelled }));

    // A child that exits without reading stdin raises EPIPE; that is not an error here.
    child.stdin!.on("error", () => undefined);
    child.stdin!.end(opts.input ?? "");
  });
}

/** Last meaningful lines of a process's stderr, for user-facing error messages. */
export function tailOf(text: string, lines = 6): string {
  return text
    .split(/\r?\n/)
    .map((l) => l.trimEnd())
    .filter((l) => l && !/^\s*warning: skill /.test(l))
    .slice(-lines)
    .join("\n");
}
