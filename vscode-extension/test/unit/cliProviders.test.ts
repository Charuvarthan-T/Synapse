import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
  completeWithClaude,
  completeWithCodex,
  discoverClaude,
  findOnPath,
  parseClaudeEnvelope,
} from "../../src/ai/cliProviders";
import { FatalProviderError } from "../../src/ai/bridgeServer";

const fakeDir = path.resolve(__dirname, "../../../test/fixtures/fake-cli");
const bin = (name: string) => path.join(fakeDir, process.platform === "win32" ? `${name}.cmd` : name);
const signal = () => new AbortController().signal;
const PROMPT = 'Line one with "quotes" & ampersand\nline two: 100% <tags>';

describe("CLI providers (Claude Code / Codex)", function () {
  this.timeout(20000);

  afterEach(() => {
    delete process.env.FAKE_CLAUDE_MODE;
    delete process.env.FAKE_CODEX_MODE;
  });

  it("parses Claude Code JSON envelopes, including event arrays", () => {
    const one = parseClaudeEnvelope(JSON.stringify({ result: "hi", usage: { input_tokens: 1, cache_read_input_tokens: 2, output_tokens: 3 } }));
    assert.deepStrictEqual(one, { text: "hi", usage: { input_tokens: 3, output_tokens: 3 } });
    const many = parseClaudeEnvelope(JSON.stringify([{ type: "system" }, { type: "result", result: "last" }]));
    assert.strictEqual(many.text, "last");
    assert.throws(() => parseClaudeEnvelope("not json"), /unreadable/);
    assert.throws(() => parseClaudeEnvelope(JSON.stringify({ is_error: true, result: "Please run /login" })), FatalProviderError);
  });

  it("runs Claude Code in lean, tool-free mode with the prompt on stdin", async () => {
    const r = await completeWithClaude(bin("claude"), { prompt: PROMPT, maxTokens: 100 }, "sonnet", signal());
    const echoed = JSON.parse(r.text);
    assert.strictEqual(echoed.input, PROMPT, "prompt must arrive intact via stdin, not argv");
    const args: string[] = echoed.args;
    assert.deepStrictEqual(args.slice(0, 4), ["-p", "--output-format", "json", "--no-session-persistence"]);
    assert.strictEqual(args[args.indexOf("--tools") + 1], "", "empty --tools value must survive (cmd.exe on Windows)");
    assert.ok(args.includes("--strict-mcp-config"));
    assert.match(args[args.indexOf("--system-prompt") + 1], /^You are a precise software analysis assistant\./);
    assert.deepStrictEqual(args.slice(-2), ["--model", "sonnet"]);
    assert.deepStrictEqual(r.usage, { input_tokens: 7, output_tokens: 7 });
  });

  it("falls back to basic flags for older Claude Code versions", async () => {
    process.env.FAKE_CLAUDE_MODE = "old";
    const r = await completeWithClaude(bin("claude"), { prompt: "q", maxTokens: 10 }, undefined, signal());
    assert.deepStrictEqual(JSON.parse(r.text).args, ["-p", "--output-format", "json"]);
  });

  it("reports sign-in problems as fatal (no retries)", async () => {
    process.env.FAKE_CLAUDE_MODE = "auth";
    await assert.rejects(completeWithClaude(bin("claude"), { prompt: "q", maxTokens: 10 }, undefined, signal()), FatalProviderError);
  });

  it("rejects unsafe model names before spawning anything", async () => {
    await assert.rejects(completeWithClaude(bin("claude"), { prompt: "q", maxTokens: 10 }, 'x" & calc', signal()), /Invalid model name/);
  });

  it("runs Codex read-only in an empty scratch directory and reads the answer file", async () => {
    const r = await completeWithCodex(bin("codex"), { prompt: PROMPT, maxTokens: 100 }, undefined, signal());
    const echoed = JSON.parse(r.text);
    assert.ok(echoed.input.endsWith(PROMPT));
    assert.deepStrictEqual(echoed.args.slice(0, 2), ["exec", "--skip-git-repo-check"]);
    assert.strictEqual(echoed.args[echoed.args.indexOf("--sandbox") + 1], "read-only");
    assert.ok(echoed.args.includes("--ephemeral"));
    assert.strictEqual(echoed.args[echoed.args.length - 1], "-");
    assert.ok(path.basename(echoed.cwd).startsWith("synapse-codex-"));
    assert.ok(!fs.existsSync(echoed.cwd), "scratch directory is removed afterwards");
  });

  it("falls back for older Codex versions without --ephemeral", async () => {
    process.env.FAKE_CODEX_MODE = "old";
    const r = await completeWithCodex(bin("codex"), { prompt: "q", maxTokens: 10 }, undefined, signal());
    assert.ok(!JSON.parse(r.text).args.includes("--ephemeral"));
  });

  it("finds executables on PATH (preferring .exe over .cmd on Windows)", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-path-"));
    try {
      const names = process.platform === "win32" ? ["tool.cmd", "tool.exe"] : ["tool"];
      for (const n of names) fs.writeFileSync(path.join(dir, n), "");
      const found = findOnPath("tool", `${path.join(dir, "missing")}${path.delimiter}${dir}`);
      assert.strictEqual(found, path.join(dir, process.platform === "win32" ? "tool.exe" : "tool"));
      assert.strictEqual(findOnPath("nope", dir), undefined);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("prefers the Claude binary bundled with the Claude Code extension", () => {
    const ext = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-ext-"));
    try {
      const exe = path.join(ext, "resources", "native-binary", process.platform === "win32" ? "claude.exe" : "claude");
      fs.mkdirSync(path.dirname(exe), { recursive: true });
      fs.writeFileSync(exe, "");
      assert.strictEqual(discoverClaude(ext), exe);
    } finally {
      fs.rmSync(ext, { recursive: true, force: true });
    }
  });
});
