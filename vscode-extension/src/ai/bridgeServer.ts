import * as crypto from "crypto";
import * as http from "http";

// Pure Node (no `vscode` import). A loopback HTTP endpoint the Python engine's
// `editor-bridge` backend calls for each LLM completion, so the engine can use
// whichever assistant the editor is signed in to, without any API key.
//
// Security: bound to 127.0.0.1 on a random port, alive only for one engine
// run, and every request must carry a fresh 256-bit bearer token.

export interface CompletionRequest {
  prompt: string;
  maxTokens: number;
  model?: string;
}

export interface CompletionResult {
  text: string;
  usage?: { input_tokens?: number; output_tokens?: number };
}

export type CompletionHandler = (req: CompletionRequest, signal: AbortSignal) => Promise<CompletionResult>;

/** An error that will fail every later request too (not signed in, consent
 * denied, CLI missing), so the engine's retry loop fails fast instead of
 * re-prompting the user or re-spawning a broken CLI. */
export class FatalProviderError extends Error {
  readonly fatal = true;
}

export interface Bridge {
  url: string;
  token: string;
  /** Environment variables that point the engine at this bridge. */
  env: Record<string, string>;
  /** Number of completion requests received so far. */
  readonly requests: number;
  close(): Promise<void>;
}

const MAX_BODY_BYTES = 8 * 1024 * 1024;

function send(res: http.ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body), "utf8");
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": data.length });
  res.end(data);
}

function tokenMatches(header: string | undefined, token: string): boolean {
  const expected = Buffer.from(`Bearer ${token}`);
  const given = Buffer.from(header ?? "");
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

export async function startBridge(
  handler: CompletionHandler,
  onRequest?: (count: number) => void
): Promise<Bridge> {
  const token = crypto.randomBytes(32).toString("hex");
  const abort = new AbortController();
  let requests = 0;
  let fatal: string | undefined;

  const server = http.createServer((req, res) => {
    if (req.method !== "POST" || req.url !== "/complete") return send(res, 404, { error: "not found" });
    if (!tokenMatches(req.headers.authorization, token)) return send(res, 401, { error: "unauthorized" });

    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        send(res, 413, { error: "request too large" });
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", async () => {
      if (res.headersSent) return;
      let body: { prompt?: unknown; max_tokens?: unknown; model?: unknown };
      try {
        body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        return send(res, 400, { error: "invalid JSON" });
      }
      if (typeof body.prompt !== "string" || !body.prompt) return send(res, 400, { error: "missing prompt" });
      if (fatal) return send(res, 503, { error: fatal });

      requests += 1;
      onRequest?.(requests);
      try {
        const result = await handler(
          {
            prompt: body.prompt,
            maxTokens: typeof body.max_tokens === "number" ? body.max_tokens : 2000,
            model: typeof body.model === "string" && body.model ? body.model : undefined,
          },
          abort.signal
        );
        send(res, 200, { text: result.text, usage: result.usage ?? {} });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (err instanceof FatalProviderError) fatal = message;
        send(res, err instanceof FatalProviderError ? 503 : 502, { error: message });
      }
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const { port } = server.address() as { port: number };
  const url = `http://127.0.0.1:${port}/complete`;

  return {
    url,
    token,
    env: { SYNAPSE_BRIDGE_URL: url, SYNAPSE_BRIDGE_TOKEN: token },
    get requests() {
      return requests;
    },
    close: () =>
      new Promise<void>((resolve) => {
        abort.abort();
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
