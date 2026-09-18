import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";
import { parseExplainOutput, parseSearchOutput } from "../../src/engine/parsers";
import { asArgument } from "../../src/engine/engine";

// Fixtures are real output captured from the Synapse engine CLI.
const fixture = (name: string) =>
  fs.readFileSync(path.resolve(__dirname, "../../../test/fixtures/engine-output", name), "utf8");

describe("engine output parsers", () => {
  it("parses `graphify query` output into nodes, edges and strategy", () => {
    const r = parseSearchOutput(fixture("query.txt"));
    assert.match(r.strategy!, /Weighted relations/);
    assert.ok(r.nodes.length >= 10);
    const login = r.nodes.find((n) => n.label === "login()");
    assert.deepStrictEqual(login, { label: "login()", file: "app/auth.py", line: 15, community: "Community 0" });
    const call = r.edges.find((e) => e.from === "login()" && e.to === "create_session()");
    assert.deepStrictEqual(call, {
      from: "login()",
      relation: "calls",
      confidence: "EXTRACTED",
      to: "create_session()",
      file: "app/auth.py",
      line: 18,
    });
  });

  it("returns empty results for 'No matching nodes found.'", () => {
    const r = parseSearchOutput("No matching nodes found.");
    assert.strictEqual(r.nodes.length, 0);
    assert.strictEqual(r.edges.length, 0);
  });

  it("parses `graphify explain` output", () => {
    const r = parseExplainOutput(fixture("explain.txt"));
    assert.strictEqual(r.found, true);
    assert.strictEqual(r.node!.label, "login()");
    assert.strictEqual(r.node!.file, "app/auth.py");
    assert.strictEqual(r.node!.line, 15);
    assert.strictEqual(r.node!.degree, 4);
    assert.strictEqual(r.connections.length, 4);
    assert.deepStrictEqual(r.connections.find((c) => c.direction === "in"), {
      direction: "in",
      label: "auth.py",
      relation: "contains",
      confidence: "EXTRACTED",
      file: "app/auth.py",
      line: 15,
    });
  });

  it("reports not-found explain output", () => {
    const r = parseExplainOutput("No node matching 'nope' found.");
    assert.strictEqual(r.found, false);
  });

  it("keeps free-text questions from being parsed as CLI flags", () => {
    assert.strictEqual(asArgument("--unweighted"), " --unweighted");
    assert.strictEqual(asArgument("  how   does\nlogin work "), "how does login work");
  });
});
