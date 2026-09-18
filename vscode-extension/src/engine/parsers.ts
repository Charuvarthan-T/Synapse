// Pure parsers for the engine's text output (no `vscode` import).

export interface GraphRef {
  label: string;
  file?: string;
  line?: number;
  community?: string;
}

export interface EdgeRef {
  from: string;
  relation: string;
  confidence?: string;
  to: string;
  file?: string;
  line?: number;
}

export interface SearchResult {
  /** Retrieval strategy line, e.g. "BFS depth=2 | Weighted relations | ...". */
  strategy?: string;
  nodes: GraphRef[];
  edges: EdgeRef[];
  /** The engine's raw text, which is also what Copilot receives. */
  raw: string;
}

export interface ExplainResult {
  found: boolean;
  node?: GraphRef & { type?: string; degree?: number };
  connections: { direction: "out" | "in"; label: string; relation: string; confidence?: string; file?: string; line?: number }[];
  raw: string;
}

function parseLine(loc: string | undefined): number | undefined {
  const m = loc?.match(/L(\d+)/);
  return m ? Number(m[1]) : undefined;
}

/** Split "path/to/file.py:L12" into file and line. */
function parseAt(at: string | undefined): { file?: string; line?: number } {
  if (!at) return {};
  const m = at.match(/^(.*?)(?::L(\d+))?$/);
  return { file: m?.[1] || undefined, line: m?.[2] ? Number(m[2]) : undefined };
}

/** key=value pairs where values may contain spaces ("community=Community 2"). */
function parseAttrs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /(\w+)=(.*?)(?=\s+\w+=|$)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) out[m[1]] = m[2].trim();
  return out;
}

export function parseSearchOutput(raw: string): SearchResult {
  const result: SearchResult = { nodes: [], edges: [], raw };
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("Traversal:")) {
      result.strategy = line.slice("Traversal:".length).trim();
      continue;
    }
    const node = line.match(/^NODE (.*) \[(.*)\]\s*$/);
    if (node) {
      const a = parseAttrs(node[2]);
      result.nodes.push({ label: node[1], file: a.src || undefined, line: parseLine(a.loc), community: a.community });
      continue;
    }
    const edge = line.match(/^EDGE (.+?) --(\S+) \[(.*?)\]--> (.+?)(?: at=(\S.*))?\s*$/);
    if (edge) {
      result.edges.push({
        from: edge[1],
        relation: edge[2],
        confidence: edge[3].split(/\s+/)[0] || undefined,
        to: edge[4],
        ...parseAt(edge[5]),
      });
    }
  }
  return result;
}

export function parseExplainOutput(raw: string): ExplainResult {
  const result: ExplainResult = { found: false, connections: [], raw };
  const field = (name: string) => raw.match(new RegExp(`^\\s*${name}:\\s*(.*)$`, "m"))?.[1]?.trim();
  const label = field("Node");
  if (!label) return result;
  const source = field("Source");
  const sm = source?.match(/^(.*?)(?:\s+(L\d+))?$/);
  result.found = true;
  result.node = {
    label,
    file: sm?.[1] || undefined,
    line: parseLine(sm?.[2]),
    community: field("Community"),
    type: field("Type"),
    degree: field("Degree") ? Number(field("Degree")) : undefined,
  };
  for (const line of raw.split(/\r?\n/)) {
    const m = line.match(/^\s*(-->|<--) (.+?) \[([^\]]+)\] \[([^\]]+)\](?:\s+(\S.*))?$/);
    if (m) {
      result.connections.push({
        direction: m[1] === "-->" ? "out" : "in",
        label: m[2],
        relation: m[3],
        confidence: m[4],
        ...parseAt(m[5]?.trim()),
      });
    }
  }
  return result;
}
