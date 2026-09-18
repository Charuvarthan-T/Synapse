// Pure view-model builders (no `vscode` import): engine results in, the
// objects the results webview renders out. Kept separate so the wording and
// verdict logic are unit-tested.
import type { ReasonResult, VerifyResult, Verdicted } from "../engine/engine";
import type { ExplainResult, SearchResult } from "../engine/parsers";
import { escapeHtml, renderMarkdown } from "./markdown";

export type Level = "ok" | "warn" | "bad" | "none";

export interface Verdict {
  level: Level;
  headline: string;
  detail?: string;
}

export interface Ref {
  label: string;
  file?: string;
  line?: number;
}

export interface ClaimView {
  verdict: Verdicted["verdict"];
  /** Readable form, e.g. "login() calls hash_password()". */
  text: string;
  /** The engine's claim notation, e.g. "login() -[calls]→ hash_password()". */
  raw: string;
  reason: string;
  refs: Ref[];
}

/** Resolves graph node ids and labels for linking; supplied by the extension. */
export interface NodeLookup {
  byId(id: string): Ref | undefined;
  hasLabel(label: string): boolean;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

const RELATION_VERBS: Record<string, string> = {
  calls: "calls",
  imports: "imports",
  imports_from: "imports",
  inherits: "inherits from",
  extends: "extends",
  implements: "implements",
  contains: "contains",
  method: "has method",
  references: "references",
  uses: "uses",
};

/** Turn the engine's claim notation into a sentence. */
export function humanizeClaim(claim: string): string {
  const exists = claim.match(/^exists:(.+)$/);
  if (exists) return `${exists[1]} exists`;
  const method = claim.match(/^method:(.+)\.([^.]+)$/);
  if (method) return `${method[2]} is a member of ${method[1]}`;
  const edge = claim.match(/^(.+?) -\[([\w-]+)\]→ (.+)$/);
  if (edge) return `${edge[1]} ${RELATION_VERBS[edge[2]] ?? edge[2].replace(/_/g, " ")} ${edge[3]}`;
  return claim;
}

/** Make plain-text mentions of real graph symbols ("login()") clickable.
 * Operates only on text between tags, never inside <code> or <pre>. */
export function linkSymbols(html: string, isSymbol: (label: string) => boolean): string {
  let inCode = 0;
  return html
    .split(/(<[^>]+>)/g)
    .map((part) => {
      if (part.startsWith("<")) {
        if (/^<(code|pre)[\s>]/.test(part)) inCode++;
        else if (/^<\/(code|pre)>/.test(part)) inCode = Math.max(0, inCode - 1);
        return part;
      }
      if (inCode) return part;
      return part.replace(/(^|[^\w.])([A-Za-z_][\w.]*\(\))/g, (m, pre: string, label: string) =>
        isSymbol(label) ? `${pre}<code class="symbol" title="Go to ${label}">${label}</code>` : m
      );
    })
    .join("");
}

/** The engine appends a "--- Graph validation:" appendix; the UI shows that
 * structurally instead, so keep only the answer body. */
export function answerBody(finalAnswer: string): string {
  const cut = finalAnswer.search(/\n-{3,}\s*\nGraph validation:/);
  return (cut >= 0 ? finalAnswer.slice(0, cut) : finalAnswer).trim();
}

function toClaimView(r: Verdicted, lookup: NodeLookup): ClaimView {
  const refs: Ref[] = [];
  const seen = new Set<string>();
  for (const ev of r.evidence ?? []) {
    for (const id of ev.node_ids ?? []) {
      const ref = lookup.byId(id);
      if (ref && !seen.has(id)) {
        seen.add(id);
        refs.push(ref);
      }
    }
  }
  return { verdict: r.verdict, text: humanizeClaim(r.claim), raw: r.claim, reason: r.reason, refs: refs.slice(0, 4) };
}

/** Drop repeated checks (the engine may test one reference two ways). */
export function dedupeChecks(results: Verdicted[]): Verdicted[] {
  const seen = new Set<string>();
  return results.filter((r) => {
    const key = `${r.verdict}|${r.claim}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function reasonVerdict(result: ReasonResult): Verdict {
  const { supported: s, contradicted: c, unknown: u } = result.validation.counts;
  const total = s + c + u;
  if (total === 0) {
    return { level: "none", headline: "No checkable claims", detail: "The answer didn't make claims the graph can verify, so read it as unverified." };
  }
  if (c > 0) {
    return {
      level: "bad",
      headline: `The graph contradicts ${plural(c, "claim")}`,
      detail: result.revised
        ? "Synapse fed the contradictions back and the answer was revised. Check the flagged claims below."
        : "Treat the contradicted statements as unreliable.",
    };
  }
  if (u > 0) {
    return {
      level: "warn",
      headline: `${s} of ${plural(total, "claim")} verified`,
      detail: `${plural(u, "claim")} couldn't be confirmed from the graph, which doesn't make them false.`,
    };
  }
  return { level: "ok", headline: `All ${plural(total, "claim")} verified by the graph` };
}

export function answerView(result: ReasonResult, providerLabel: string, lookup: NodeLookup) {
  const text = answerBody(result.final_answer || result.draft_answer);
  const marked = renderMarkdown(text).replace(/<code data-symbol="([^"]*)">/g, (_m, sym: string) => {
    const label = sym.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    return lookup.hasLabel(label) ? `<code class="symbol" title="Go to ${escapeHtml(label)}">` : "<code>";
  });
  const html = linkSymbols(marked, (label) => lookup.hasLabel(label));
  const order = { CONTRADICTED: 0, UNKNOWN: 1, SUPPORTED: 2 } as const;
  const claims = dedupeChecks(result.validation.results)
    .sort((a, b) => order[a.verdict] - order[b.verdict])
    .map((r) => toClaimView(r, lookup));
  return {
    kind: "answer" as const,
    question: result.question,
    providerLabel,
    answerHtml: html,
    answerText: text,
    revised: result.revised,
    verdict: reasonVerdict(result),
    claims,
    contextChars: result.graph_context_chars,
  };
}

export function verifyVerdict(counts: { supported: number; contradicted: number; unknown: number }): Verdict {
  const { supported: s, contradicted: c, unknown: u } = counts;
  if (c > 0) {
    return {
      level: "bad",
      headline: `${plural(c, "reference")} contradict${c === 1 ? "s" : ""} the graph`,
      detail: "The graph knows the owner but not this member. These are likely hallucinated APIs.",
    };
  }
  if (s > 0) {
    return {
      level: "ok",
      headline: "No contradictions found",
      detail: `${plural(s, "reference")} confirmed${u ? `; ${u} not in the graph (external libraries, built-ins or new code)` : ""}.`,
    };
  }
  return {
    level: "none",
    headline: "Nothing to confirm",
    detail: u ? "None of the references are defined in this repository." : "No imports, calls or methods were found to check.",
  };
}

export function verifyView(result: VerifyResult, source: string, lines: number, lookup: NodeLookup) {
  const checks = dedupeChecks(result.checks.results);
  const pick = (v: Verdicted["verdict"]) => checks.filter((r) => r.verdict === v).map((r) => toClaimView(r, lookup));
  const contradicted = pick("CONTRADICTED");
  const supported = pick("SUPPORTED");
  const unknown = pick("UNKNOWN");
  return {
    kind: "verify" as const,
    source,
    lines,
    verdict: verifyVerdict({ supported: supported.length, contradicted: contradicted.length, unknown: unknown.length }),
    contradicted,
    unknown,
    supported,
    notes: result.notes,
  };
}

/** "BFS depth=2 | Weighted relations | Community fallback (...) | Start: [...] | 15 nodes found" -> short label. */
export function shortStrategy(strategy: string | undefined): string | undefined {
  if (!strategy) return undefined;
  const parts = strategy.split("|").map((p) => p.trim());
  const labels: string[] = [];
  if (parts.some((p) => /^weighted/i.test(p))) labels.push("Weighted");
  if (parts.some((p) => /^community/i.test(p))) labels.push(/fallback/i.test(strategy) ? "Community-aware (broadened)" : "Community-aware");
  const bfs = parts.find((p) => /depth=\d+/.test(p));
  if (bfs) labels.push(bfs.replace(/^(BFS|DFS)\s+depth=(\d+)/i, "$1 · depth $2"));
  return labels.join(" · ") || undefined;
}

export function searchView(question: string, result: SearchResult) {
  return {
    kind: "search" as const,
    question,
    strategy: result.strategy,
    strategyShort: shortStrategy(result.strategy),
    nodes: result.nodes,
    edges: result.edges,
  };
}

const RELATION_TITLES: Record<string, [string, string]> = {
  calls: ["Calls", "Called by"],
  imports: ["Imports", "Imported by"],
  imports_from: ["Imports", "Imported by"],
  contains: ["Contains", "Defined in"],
  method: ["Methods", "Member of"],
  inherits: ["Inherits from", "Subclasses"],
  extends: ["Extends", "Extended by"],
  implements: ["Implements", "Implemented by"],
  references: ["References", "Referenced by"],
  uses: ["Uses", "Used by"],
};

export function explainView(symbol: string, result: ExplainResult) {
  if (!result.found || !result.node) return { kind: "explain" as const, symbol, node: null, groups: [] };
  const groups = new Map<string, { title: string; items: Ref[] }>();
  for (const c of result.connections) {
    const titles = RELATION_TITLES[c.relation];
    const title = titles ? titles[c.direction === "out" ? 0 : 1] : c.direction === "out" ? `${c.relation} →` : `← ${c.relation}`;
    if (!groups.has(title)) groups.set(title, { title, items: [] });
    groups.get(title)!.items.push({ label: c.label, file: c.file, line: c.line });
  }
  return { kind: "explain" as const, symbol, node: result.node, groups: [...groups.values()] };
}
