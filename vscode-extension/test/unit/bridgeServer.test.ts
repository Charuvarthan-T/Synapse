import * as assert from "assert";
import * as http from "http";
import { FatalProviderError, startBridge } from "../../src/ai/bridgeServer";

function post(url: string, token: string | null, body: unknown): Promise<{ status: number; json: any }> {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
    const req = http.request(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": data.length,
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (res) => {
        let text = "";
        res.on("data", (c) => (text += c));
        res.on("end", () => resolve({ status: res.statusCode!, json: text ? JSON.parse(text) : null }));
      }
    );
    req.on("error", reject);
    req.end(data);
  });
}

describe("bridge server", () => {
  it("binds to loopback and answers authenticated completion requests", async () => {
    const seen: unknown[] = [];
    const bridge = await startBridge(async (req) => {
      seen.push(req);
      return { text: `echo:${req.prompt}`, usage: { input_tokens: 3, output_tokens: 1 } };
    });
    try {
      assert.match(bridge.url, /^http:\/\/127\.0\.0\.1:\d+\/complete$/);
      assert.strictEqual(bridge.token.length, 64);
      assert.deepStrictEqual(Object.keys(bridge.env).sort(), ["SYNAPSE_BRIDGE_TOKEN", "SYNAPSE_BRIDGE_URL"]);
      const r = await post(bridge.url, bridge.token, { prompt: "hi", max_tokens: 9, model: null });
      assert.strictEqual(r.status, 200);
      assert.deepStrictEqual(r.json, { text: "echo:hi", usage: { input_tokens: 3, output_tokens: 1 } });
      assert.deepStrictEqual(seen, [{ prompt: "hi", maxTokens: 9, model: undefined }]);
      assert.strictEqual(bridge.requests, 1);
    } finally {
      await bridge.close();
    }
  });

  it("rejects missing or wrong tokens and bad requests without calling the provider", async () => {
    let calls = 0;
    const bridge = await startBridge(async () => {
      calls++;
      return { text: "x" };
    });
    try {
      assert.strictEqual((await post(bridge.url, null, { prompt: "a" })).status, 401);
      assert.strictEqual((await post(bridge.url, "0".repeat(64), { prompt: "a" })).status, 401);
      assert.strictEqual((await post(bridge.url, bridge.token, "{not json")).status, 400);
      assert.strictEqual((await post(bridge.url, bridge.token, { prompt: "" })).status, 400);
      assert.strictEqual((await post(bridge.url.replace("/complete", "/other"), bridge.token, { prompt: "a" })).status, 404);
      assert.strictEqual(calls, 0);
    } finally {
      await bridge.close();
    }
  });

  it("returns 502 for a transient provider error and keeps serving", async () => {
    let n = 0;
    const bridge = await startBridge(async () => {
      if (++n === 1) throw new Error("rate limited");
      return { text: "ok" };
    });
    try {
      const first = await post(bridge.url, bridge.token, { prompt: "a" });
      assert.deepStrictEqual([first.status, first.json.error], [502, "rate limited"]);
      assert.strictEqual((await post(bridge.url, bridge.token, { prompt: "a" })).status, 200);
    } finally {
      await bridge.close();
    }
  });

  it("latches fatal provider errors so retries fail fast", async () => {
    let calls = 0;
    const bridge = await startBridge(async () => {
      calls++;
      throw new FatalProviderError("not signed in");
    });
    try {
      for (let i = 0; i < 3; i++) {
        const r = await post(bridge.url, bridge.token, { prompt: "a" });
        assert.deepStrictEqual([r.status, r.json.error], [503, "not signed in"]);
      }
      assert.strictEqual(calls, 1);
    } finally {
      await bridge.close();
    }
  });

  it("aborts in-flight provider calls when closed", async () => {
    let aborted = false;
    const bridge = await startBridge(
      (_req, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            aborted = true;
            reject(new Error("aborted"));
          });
        })
    );
    const pending = post(bridge.url, bridge.token, { prompt: "a" }).catch(() => null);
    await new Promise((r) => setTimeout(r, 100));
    await bridge.close();
    await pending;
    assert.strictEqual(aborted, true);
  });
});
