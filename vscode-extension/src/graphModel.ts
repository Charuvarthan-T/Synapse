import * as fs from "fs";

// Deliberately no dependency on the vscode module here: this file is pure
// graph data logic and is unit-tested directly under plain Node/Mocha
// (no Electron test host required). Diagnostics go through this tiny
// injectable logger instead of importing ../logger.
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
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const MAX_GRAPH_BYTES = 200 * 1024 * 1024; // 200MB sanity cap — don't try to
// JSON.parse an unreasonably large file into memory on the extension host.

export function loadGraph(graphJsonPath: string): GraphData | null {
  try {
    const stat = fs.statSync(graphJsonPath);
    if (stat.size > MAX_GRAPH_BYTES) {
      warn(`graph.json too large to load in-editor (${stat.size} bytes), skipping tree view`);
      return null;
    }
    const raw = fs.readFileSync(graphJsonPath, { encoding: "utf-8" });
    const data = JSON.parse(raw);
    const nodes: GraphNode[] = Array.isArray(data.nodes) ? data.nodes : [];
    const rawEdges = Array.isArray(data.links) ? data.links : data.edges;
    const edges: GraphEdge[] = Array.isArray(rawEdges)
      ? rawEdges.map((e: any) => ({
          source: e.source,
          target: e.target,
          relation: e.relation,
        }))
      : [];
    return { nodes, edges };
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
