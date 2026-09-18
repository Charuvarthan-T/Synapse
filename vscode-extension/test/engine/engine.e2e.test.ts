// End-to-end tests against the real Synapse engine: provisions a private venv
// from the wheel bundled in engine/ (run `npm run build:engine` first), builds
// a multi-language workspace and drives every engine feature the extension
// uses. The AI is a scripted fake behind the real bridge, so no account or
// network access to an AI service is needed.
import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { Engine, EngineError, graphHtmlPath, graphPath } from "../../src/engine/engine";
import { venvDir } from "../../src/engine/provisioner";
import { CompletionHandler, CompletionRequest, FatalProviderError } from "../../src/ai/bridgeServer";
import { computeStats, loadGraph } from "../../src/graphModel";

const extRoot = path.resolve(__dirname, "../../..");
const fixture = path.join(extRoot, "test", "fixtures", "sample-workspace");
// Short path on purpose: long venv paths break pip on Windows (MAX_PATH).
const home = process.env.SYNAPSE_TEST_HOME || path.join(os.homedir(), ".synapse-e2e-test");

function copyDir(src: string, dest: string): void {
  fs.mkdirSync(dest, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const from = path.join(src, e.name);
    const to = path.join(dest, e.name);
    if (e.isDirectory()) copyDir(from, to);
    else fs.copyFileSync(from, to);
  }
}

describe("Synapse engine (end to end)", function () {
  this.timeout(15 * 60 * 1000);
  const logs: string[] = [];
  const engine = new Engine(() => ({ extensionPath: extRoot, home, log: (m) => logs.push(m) }));
  let ws: string;

  before(() => {
    assert.ok(fs.existsSync(path.join(extRoot, "engine", "manifest.json")), "run `npm run build:engine` first");
    ws = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-ws-"));
    copyDir(fixture, ws);
  });

  after(() => {
    fs.rmSync(ws, { recursive: true, force: true });
  });

  it("provisions the bundled Synapse engine into a private venv", async () => {
    const exe = await engine.ensure(undefined, (m) => logs.push(`progress: ${m}`));
    assert.ok(fs.existsSync(exe));
    const marker = JSON.parse(fs.readFileSync(path.join(venvDir(home), "synapse-engine.json"), "utf8"));
    const manifest = JSON.parse(fs.readFileSync(path.join(extRoot, "engine", "manifest.json"), "utf8"));
    assert.strictEqual(marker.engine, manifest.sha256, "installed engine must be the bundled wheel");
  });

  it("reinstalls when the installed engine differs from the bundled one (upgrade from V1)", async () => {
    const markerFile = path.join(venvDir(home), "synapse-engine.json");
    fs.writeFileSync(markerFile, JSON.stringify({ engine: "upstream-graphifyy-0.9.63" }));
    const fresh = new Engine(() => ({ extensionPath: extRoot, home, log: (m) => logs.push(m) }));
    await fresh.ensure();
    const manifest = JSON.parse(fs.readFileSync(path.join(extRoot, "engine", "manifest.json"), "utf8"));
    assert.strictEqual(JSON.parse(fs.readFileSync(markerFile, "utf8")).engine, manifest.sha256);
  });

  it("builds a graph of a multi-language workspace, locally", async () => {
    const progress: string[] = [];
    await engine.build(ws, undefined, (m) => progress.push(m));
    assert.ok(fs.existsSync(graphPath(ws)));
    assert.ok(fs.existsSync(graphHtmlPath(ws)), "graph map must be generated");
    const stats = computeStats(loadGraph(graphPath(ws))!);
    assert.ok(stats.nodes >= 15, `expected >= 15 nodes, got ${stats.nodes}`);
    assert.ok(stats.files >= 3, "python and typescript files are both indexed");
    assert.ok(progress.some((p) => /Parsing \d+ files/.test(p)), progress.join(" | "));
  });

  it("searches with weighted, community-aware retrieval", async () => {
    const r = await engine.search(ws, "how does login work", 1500);
    assert.match(r.strategy ?? "", /Weighted/);
    assert.match(r.strategy ?? "", /Community/i);
    const labels = r.nodes.map((n) => n.label);
    assert.ok(labels.includes("login()"), labels.join(", "));
    assert.ok(r.edges.some((e) => e.from === "login()" && e.relation === "calls" && e.to === "hash_password()"));
  });

  it("explains a symbol and its neighbours", async () => {
    const r = await engine.explain(ws, "login");
    assert.ok(r.found);
    assert.strictEqual(r.node!.file, "app/auth.py");
    assert.ok(r.connections.some((c) => c.label === "create_session()" && c.relation === "calls"));
  });

  it("flags hallucinated APIs with pre-execution verification", async () => {
    const code = [
      "from app.db import Database",
      "db = Database()",
      'db.find_user("a")',
      "db.delete_everything()",
    ].join("\n");
    const r = await engine.verify(ws, code);
    const bad = r.checks.results.filter((c) => c.verdict === "CONTRADICTED").map((c) => c.claim);
    assert.ok(bad.some((c) => c.includes("delete_everything")), JSON.stringify(r.checks.results, null, 1));
    assert.ok(r.checks.results.some((c) => c.verdict === "SUPPORTED" && c.claim.includes("find_user")));
  });

  it("runs bidirectional reasoning through the editor bridge, revising contradicted claims", async () => {
    const prompts: CompletionRequest[] = [];
    const fakeAi: CompletionHandler = async (req) => {
      prompts.push(req);
      if (prompts.length === 1) {
        return {
          text: JSON.stringify({
            answer: "`login()` hashes the password with `hash_password()` and wipes the DB.",
            claims: [
              { type: "calls", source: "login", target: "hash_password" },
              { type: "calls", source: "login", target: "delete_everything" },
            ],
          }),
        };
      }
      return { text: JSON.stringify({ answer: "`login()` verifies the password via `hash_password()`.", claims: [] }) };
    };
    const requests: number[] = [];
    const r = await engine.reason(ws, "how does login work", fakeAi, undefined, (n) => requests.push(n));
    assert.strictEqual(prompts.length, 2, "draft + revision");
    assert.match(prompts[0].prompt, /GRAPH CONTEXT/);
    assert.match(prompts[0].prompt, /login\(\)/, "retrieved graph context is sent to the AI");
    assert.match(prompts[1].prompt, /VALIDATION REPORT/);
    assert.strictEqual(r.revised, true);
    assert.ok(r.validation.counts.supported >= 1);
    assert.match(r.final_answer, /verifies the password/);
    assert.deepStrictEqual(requests, [1, 2]);
  });

  it("surfaces the real AI error instead of a generic engine message", async () => {
    let calls = 0;
    const deniedAi: CompletionHandler = async () => {
      calls++;
      throw new FatalProviderError("Synapse doesn't have permission to use GPT-5 mini.");
    };
    await assert.rejects(engine.reason(ws, "how does login work", deniedAi), (err: unknown) => {
      assert.ok(err instanceof EngineError);
      assert.strictEqual(err.kind, "ai");
      assert.match(err.message, /permission to use GPT-5 mini/);
      return true;
    });
    assert.strictEqual(calls, 1, "fatal errors are not retried");
  });

  it("enriches the graph with AI intent relations that survive incremental updates", async () => {
    const fakeAi: CompletionHandler = async (req) => {
      const ids = [...req.prompt.matchAll(/^- (\S+): (.+?) @/gm)].map((m) => ({ id: m[1], label: m[2] }));
      const find = (label: string) => ids.find((u) => u.label === label)?.id;
      const relations = [];
      if (find("login()") && find("hash_password()")) {
        relations.push({ source: find("login()"), relation: "validates", target: find("hash_password()"), confidence: "INFERRED", rationale: "checks credentials" });
      }
      if (find("login()") && find("Database")) {
        relations.push({ source: find("Database"), relation: "manages", target: find("User") ?? find("login()"), confidence: "INFERRED", rationale: "stores users" });
      }
      return { text: JSON.stringify({ relations }) };
    };
    const res = await engine.enrich(ws, fakeAi);
    assert.ok(res.edgesAdded + res.edgesAugmented >= 1, res.summary);
    assert.strictEqual(res.failedRequests, 0);
    const before = computeStats(loadGraph(graphPath(ws))!).semanticRelations;
    assert.ok(before >= 1);

    fs.appendFileSync(path.join(ws, "app", "auth.py"), "\n\ndef logout(user):\n    return create_session(user)\n");
    await engine.update(ws);
    const after = computeStats(loadGraph(graphPath(ws))!);
    assert.ok(loadGraph(graphPath(ws))!.nodes.some((n) => n.label === "logout()"), "update picked up the edit");
    assert.strictEqual(after.semanticRelations, before, "AI relations must survive an incremental update");

    await engine.build(ws);
    const rebuilt = computeStats(loadGraph(graphPath(ws))!);
    assert.strictEqual(rebuilt.semanticRelations, before, "AI relations must survive Build Graph too");
  });

  it("reports an empty workspace clearly", async () => {
    const empty = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-empty-"));
    try {
      fs.writeFileSync(path.join(empty, "notes.md"), "# just notes\n");
      await assert.rejects(engine.build(empty), (err: unknown) => err instanceof EngineError && err.kind === "empty");
    } finally {
      fs.rmSync(empty, { recursive: true, force: true });
    }
  });

  it("can be cancelled", async () => {
    const abort = new AbortController();
    const pending = engine.build(ws, abort.signal);
    setTimeout(() => abort.abort(), 300);
    await assert.rejects(pending, (err: unknown) => err instanceof EngineError && err.kind === "cancelled");
  });
});
