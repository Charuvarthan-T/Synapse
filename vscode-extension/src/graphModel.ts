import * as fs from "fs";

// Pure graph-data logic (no `vscode` import), unit-tested under plain Node.
// Diagnostics go through this injectable logger instead of ../logger.
let warn: (msg: string) => void = () => {};
export function setGraphModelLogger(fn: (msg: string) => void): void {
  warn = fn;
}

export interface GraphNode {
  id: string;
  label: string;
  file_type?: string;
  source_file?: string;
  source_location?: string;
  community?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation?: string;
  /** "semantic_code" for relations added by AI enrichment. */
  origin?: string;
  /** Number of AI-inferred relations layered onto an existing structural edge. */
  secondary?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface KeySymbol {
  id: string;
  label: string;
  file?: string;
  line?: number;
  degree: number;
}

export interface GraphStats {
  nodes: number;
  edges: number;
  communities: number;
  files: number;
  /** AI-inferred relations (new edges plus relations added to existing edges). */
  semanticRelations: number;
  /** Code symbols eligible for AI enrichment. */
  codeUnits: number;
  keySymbols: KeySymbol[];
}

const MAX_GRAPH_BYTES = 200 * 1024 * 1024; // don't JSON.parse absurd files on the extension host

export const SEMANTIC_ORIGIN = "semantic_code";

export function parseGraph(data: any): GraphData {
  const nodes: GraphNode[] = Array.isArray(data?.nodes) ? data.nodes : [];
  const rawEdges = Array.isArray(data?.links) ? data.links : data?.edges;
  const edges: GraphEdge[] = Array.isArray(rawEdges)
    ? rawEdges.map((e: any) => ({
        source: e.source,
        target: e.target,
        relation: e.relation,
        origin: e._origin,
        secondary: Array.isArray(e.secondary_relations) ? e.secondary_relations.length : 0,
      }))
    : [];
  return { nodes, edges };
}

export function loadGraph(graphJsonPath: string): GraphData | null {
  try {
    const stat = fs.statSync(graphJsonPath);
    if (stat.size > MAX_GRAPH_BYTES) {
      warn(`graph.json too large to load in-editor (${stat.size} bytes)`);
      return null;
    }
    return parseGraph(JSON.parse(fs.readFileSync(graphJsonPath, { encoding: "utf-8" })));
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    warn(`failed to load graph at ${graphJsonPath}: ${detail}`);
    return null;
  }
}

export function degreeByNode(graph: GraphData): Map<string, number> {
  const degree = new Map<string, number>();
  for (const e of graph.edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }
  return degree;
}

export function topGodNodes(graph: GraphData, limit = 10): GraphNode[] {
  const degree = degreeByNode(graph);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  return [...degree.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([id]) => byId.get(id))
    .filter((n): n is GraphNode => !!n);
}

export function groupByCommunity(graph: GraphData): Map<number, GraphNode[]> {
  const groups = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const cid = n.community ?? -1;
    if (!groups.has(cid)) groups.set(cid, []);
    groups.get(cid)!.push(n);
  }
  return groups;
}

/** File nodes are labelled with the file's name or path ("auth.py", "pkg/__main__.py"). */
function isFileNode(n: GraphNode): boolean {
  if (!n.source_file || n.source_location !== "L1") return false;
  const file = n.source_file.replace(/\\/g, "/");
  const label = n.label.replace(/\\/g, "/");
  return file === label || file.endsWith(`/${label}`);
}

export function lineOf(location: string | undefined): number | undefined {
  const m = location?.match(/L(\d+)/);
  return m ? Number(m[1]) : undefined;
}

export function computeStats(graph: GraphData, keySymbolLimit = 8): GraphStats {
  const degree = degreeByNode(graph);
  const files = new Set<string>();
  let codeUnits = 0;
  for (const n of graph.nodes) {
    if (n.source_file) files.add(n.source_file);
    if (n.file_type === "code") codeUnits += 1;
  }
  const keySymbols = graph.nodes
    .filter((n) => !isFileNode(n) && n.source_file)
    .map((n) => ({ n, d: degree.get(n.id) ?? 0 }))
    .sort((a, b) => b.d - a.d || a.n.label.localeCompare(b.n.label))
    .slice(0, keySymbolLimit)
    .map(({ n, d }) => ({ id: n.id, label: n.label, file: n.source_file, line: lineOf(n.source_location), degree: d }));
  return {
    nodes: graph.nodes.length,
    edges: graph.edges.length,
    communities: [...groupByCommunity(graph).keys()].filter((c) => c >= 0).length,
    files: files.size,
    semanticRelations: graph.edges.reduce(
      (sum, e) => sum + (e.origin === SEMANTIC_ORIGIN ? 1 : 0) + (e.secondary ?? 0),
      0
    ),
    codeUnits,
    keySymbols,
  };
}

/** Caches the parsed graph by mtime so UI refreshes don't re-parse big files. */
export class GraphCache {
  private mtime = -1;
  private graph: GraphData | null = null;
  private stats: GraphStats | null = null;

  constructor(private readonly file: () => string | undefined) {}

  private load(): void {
    const file = this.file();
    let mtime = -1;
    try {
      mtime = file ? fs.statSync(file).mtimeMs : -1;
    } catch {
      mtime = -1;
    }
    if (mtime === this.mtime) return;
    this.mtime = mtime;
    this.graph = mtime >= 0 && file ? loadGraph(file) : null;
    this.stats = this.graph ? computeStats(this.graph) : null;
  }

  get(): GraphData | null {
    this.load();
    return this.graph;
  }

  getStats(): GraphStats | null {
    this.load();
    return this.stats;
  }

  findNode(id: string): GraphNode | undefined {
    return this.get()?.nodes.find((n) => n.id === id);
  }

  /** Nodes whose label matches `label` (ignoring a trailing "()" and leading "."). */
  findByLabel(label: string): GraphNode[] {
    const norm = (s: string) => s.replace(/\(\)$/, "").replace(/^\./, "");
    const want = norm(label);
    return this.get()?.nodes.filter((n) => norm(n.label) === want) ?? [];
  }
}
