import * as assert from "assert";
import type { ReasonResult, Verdicted } from "../../src/engine/engine";
import {
  answerBody,
  answerView,
  dedupeChecks,
  humanizeClaim,
  linkSymbols,
  NodeLookup,
  reasonVerdict,
  shortStrategy,
  verifyVerdict,
} from "../../src/ui/presenters";

const lookup: NodeLookup = {
  byId: (id) => (id === "app_auth_login" ? { label: "login()", file: "app/auth.py", line: 15 } : undefined),
  hasLabel: (label) => label === "login()" || label === "login",
};

function reasonResult(counts: { supported: number; contradicted: number; unknown: number }, results: Verdicted[] = []): ReasonResult {
  return {
    question: "how does login work",
    draft_answer: "draft",
    final_answer:
      "`login()` hashes the password with `hash_password` and calls `mystery()`.\n\n---\nGraph validation:\n- overall=SUPPORTED\n",
    revised: false,
    claims: [],
    validation: { verdict: "SUPPORTED", results, counts },
    parse_errors: [],
    provider_errors: [],
    graph_context_chars: 1234,
  };
}

describe("presenters", () => {
  it("strips the engine's validation appendix from the answer", () => {
    assert.strictEqual(answerBody("Answer body.\n\n---\nGraph validation:\n- x"), "Answer body.");
    assert.strictEqual(answerBody("No appendix here"), "No appendix here");
  });

  it("grades reasoning verdicts", () => {
    assert.strictEqual(reasonVerdict(reasonResult({ supported: 3, contradicted: 0, unknown: 0 })).level, "ok");
    assert.strictEqual(reasonVerdict(reasonResult({ supported: 2, contradicted: 0, unknown: 1 })).level, "warn");
    assert.strictEqual(reasonVerdict(reasonResult({ supported: 2, contradicted: 1, unknown: 0 })).level, "bad");
    assert.strictEqual(reasonVerdict(reasonResult({ supported: 0, contradicted: 0, unknown: 0 })).level, "none");
    assert.strictEqual(reasonVerdict(reasonResult({ supported: 1, contradicted: 0, unknown: 0 })).headline, "All 1 claim verified by the graph");
  });

  it("links only code spans that are real graph symbols, and orders claims worst-first", () => {
    const results: Verdicted[] = [
      { verdict: "SUPPORTED", claim: "exists:login", reason: "ok", evidence: [{ kind: "node", detail: "", node_ids: ["app_auth_login"] }] },
      { verdict: "CONTRADICTED", claim: "calls:login->mystery", reason: "no edge", evidence: [] },
    ];
    const view = answerView(reasonResult({ supported: 1, contradicted: 1, unknown: 0 }, results), "Test AI", lookup);
    assert.ok(view.answerHtml.includes('<code class="symbol" title="Go to login()">login()</code>'));
    assert.ok(view.answerHtml.includes("<code>mystery()</code>"));
    assert.ok(!view.answerText.includes("Graph validation"));
    assert.deepStrictEqual(view.claims.map((c) => c.verdict), ["CONTRADICTED", "SUPPORTED"]);
    assert.deepStrictEqual(view.claims[1].refs, [{ label: "login()", file: "app/auth.py", line: 15 }]);
  });

  it("grades verification and removes duplicate checks", () => {
    assert.strictEqual(verifyVerdict({ supported: 4, contradicted: 1, unknown: 7 }).level, "bad");
    assert.strictEqual(verifyVerdict({ supported: 4, contradicted: 1, unknown: 7 }).headline, "1 reference contradicts the graph");
    assert.strictEqual(verifyVerdict({ supported: 4, contradicted: 0, unknown: 2 }).level, "ok");
    assert.strictEqual(verifyVerdict({ supported: 0, contradicted: 0, unknown: 3 }).level, "none");
    const dup: Verdicted = { verdict: "UNKNOWN", claim: "exists:app.auth", reason: "", evidence: [] };
    assert.strictEqual(dedupeChecks([dup, { ...dup }, { ...dup, verdict: "SUPPORTED" }]).length, 2);
  });

  it("summarises the retrieval strategy", () => {
    assert.strictEqual(
      shortStrategy("BFS depth=2 | Weighted relations | Community fallback (low_confidence) | Start: ['x'] | 3 nodes found"),
      "Weighted · Community-aware (broadened) · BFS · depth 2"
    );
    assert.strictEqual(shortStrategy(undefined), undefined);
  });

  it("turns engine claim notation into sentences", () => {
    assert.strictEqual(humanizeClaim("exists:login()"), "login() exists");
    assert.strictEqual(humanizeClaim("method:auth.py.login()"), "login() is a member of auth.py");
    assert.strictEqual(humanizeClaim("method:db.delete_everything"), "delete_everything is a member of db");
    assert.strictEqual(humanizeClaim("login() -[calls]→ hash_password()"), "login() calls hash_password()");
    assert.strictEqual(humanizeClaim("A -[inherits]→ B"), "A inherits from B");
    assert.strictEqual(humanizeClaim("A -[handled_by]→ B"), "A handled by B");
    assert.strictEqual(humanizeClaim("something else"), "something else");
  });

  it("links plain-text symbol mentions, but never inside code blocks", () => {
    const isSymbol = (l: string) => l === "login()";
    const html = linkSymbols(
      "<p>First login() then nope() and x.login()</p><pre data-lang=\"py\"><code>login()</code></pre><p><code>login()</code></p>",
      isSymbol
    );
    assert.ok(html.startsWith('<p>First <code class="symbol" title="Go to login()">login()</code> then nope()'));
    assert.ok(html.includes("x.login()</p>"), "qualified names are left alone");
    assert.ok(html.includes("<pre data-lang=\"py\"><code>login()</code></pre>"), "code blocks untouched");
    assert.ok(html.endsWith("<p><code>login()</code></p>"), "inline code untouched");
  });
});
