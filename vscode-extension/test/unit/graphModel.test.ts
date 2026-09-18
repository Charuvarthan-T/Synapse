import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { loadGraph, degreeByNode, topGodNodes, groupByCommunity, computeStats, parseGraph, GraphCache, GraphData } from "../../src/graphModel";

function sampleGraph(): GraphData {
  return {
    nodes: [
      { id: "a", label: "a()", community: 0 },
      { id: "b", label: "b()", community: 0 },
      { id: "c", label: "c()", community: 1 },
      { id: "d", label: "d.py", community: 1 },
    ],
    edges: [
      { source: "a", target: "b", relation: "calls" },
      { source: "a", target: "c", relation: "calls" },
      { source: "d", target: "c", relation: "imports" },
    ],
  };
}

describe("graphModel", () => {
  it("degreeByNode counts both endpoints of each edge", () => {
    const degree = degreeByNode(sampleGraph());
    assert.strictEqual(degree.get("a"), 2);
    assert.strictEqual(degree.get("b"), 1);
    assert.strictEqual(degree.get("c"), 2);
    assert.strictEqual(degree.get("d"), 1);
  });

  it("topGodNodes ranks by degree, highest first", () => {
    const top = topGodNodes(sampleGraph(), 2);
    assert.strictEqual(top.length, 2);
    assert.ok(["a", "c"].includes(top[0].id));
    assert.ok(["a", "c"].includes(top[1].id));
  });

  it("topGodNodes respects the limit", () => {
    const top = topGodNodes(sampleGraph(), 1);
    assert.strictEqual(top.length, 1);
  });

  it("groupByCommunity groups nodes by their community id", () => {
    const groups = groupByCommunity(sampleGraph());
    assert.strictEqual(groups.size, 2);
    assert.strictEqual(groups.get(0)?.length, 2);
    assert.strictEqual(groups.get(1)?.length, 2);
  });

  it("groupByCommunity buckets nodes with no community under -1", () => {
    const graph: GraphData = {
      nodes: [{ id: "x", label: "x()" }],
      edges: [],
    };
    const groups = groupByCommunity(graph);
    assert.strictEqual(groups.get(-1)?.length, 1);
  });

  describe("loadGraph", () => {
    let tmpDir: string;

    beforeEach(() => {
      tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "graphify-test-"));
    });

    afterEach(() => {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("parses a networkx node-link graph.json (links key)", () => {
      const graphPath = path.join(tmpDir, "graph.json");
      fs.writeFileSync(
        graphPath,
        JSON.stringify({
          nodes: [{ id: "a", label: "a()" }],
          links: [{ source: "a", target: "a", relation: "self" }],
        })
      );
      const graph = loadGraph(graphPath);
      assert.ok(graph);
      assert.strictEqual(graph!.nodes.length, 1);
      assert.strictEqual(graph!.edges.length, 1);
      assert.strictEqual(graph!.edges[0].relation, "self");
    });

    it("returns null for a missing file instead of throwing", () => {
      const graph = loadGraph(path.join(tmpDir, "does-not-exist.json"));
      assert.strictEqual(graph, null);
    });

    it("returns null for malformed JSON instead of throwing", () => {
      const graphPath = path.join(tmpDir, "graph.json");
      fs.writeFileSync(graphPath, "{ not valid json");
      const graph = loadGraph(graphPath);
      assert.strictEqual(graph, null);
    });

    it("handles a graph.json with no edges array at all", () => {
      const graphPath = path.join(tmpDir, "graph.json");
      fs.writeFileSync(graphPath, JSON.stringify({ nodes: [{ id: "a", label: "a()" }] }));
      const graph = loadGraph(graphPath);
      assert.ok(graph);
      assert.strictEqual(graph!.edges.length, 0);
    });
  });

  describe("stats", () => {
    const data = {
      nodes: [
        { id: "f", label: "auth.py", source_file: "app/auth.py", source_location: "L1", file_type: "code", community: 0 },
        { id: "login", label: "login()", source_file: "app/auth.py", source_location: "L15", file_type: "code", community: 0 },
        { id: "hash", label: "hash_password()", source_file: "app/auth.py", source_location: "L11", file_type: "code", community: 0 },
        { id: "db", label: "Database", source_file: "app/db.py", source_location: "L1", file_type: "code", community: 1 },
        { id: "concept", label: "authentication", file_type: "concept" },
        { id: "main", label: "app/__main__.py", source_file: "app/__main__.py", source_location: "L1", file_type: "code", community: 1 },
      ],
      links: [
        { source: "f", target: "login", relation: "contains", _origin: "ast" },
        { source: "f", target: "hash", relation: "contains", _origin: "ast" },
        { source: "login", target: "hash", relation: "calls", _origin: "ast", secondary_relations: [{ relation: "validates" }] },
        { source: "login", target: "db", relation: "references", _origin: "ast" },
        { source: "login", target: "concept", relation: "handles", _origin: "semantic_code" },
        { source: "main", target: "login", relation: "imports", _origin: "ast" },
        { source: "main", target: "db", relation: "imports", _origin: "ast" },
        { source: "main", target: "hash", relation: "imports", _origin: "ast" },
      ],
    };

    it("counts AI-inferred relations (new edges and secondary relations)", () => {
      const stats = computeStats(parseGraph(data));
      assert.strictEqual(stats.semanticRelations, 2);
      assert.strictEqual(stats.nodes, 6);
      assert.strictEqual(stats.edges, 8);
      assert.strictEqual(stats.communities, 2);
      assert.strictEqual(stats.files, 3);
      assert.strictEqual(stats.codeUnits, 5);
    });

    it("ranks key symbols by degree, excluding file nodes and nodes without a location", () => {
      const stats = computeStats(parseGraph(data));
      assert.deepStrictEqual(stats.keySymbols[0], { id: "login", label: "login()", file: "app/auth.py", line: 15, degree: 5 });
      assert.ok(!stats.keySymbols.some((k) => ["f", "concept", "main"].includes(k.id)), "file nodes (bare or path labels) are not key symbols");
    });

    it("GraphCache re-reads only when the file changes, and finds nodes by label", () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-cache-"));
      try {
        const file = path.join(dir, "graph.json");
        fs.writeFileSync(file, JSON.stringify(data));
        const cache = new GraphCache(() => file);
        assert.strictEqual(cache.getStats()!.nodes, 6);
        assert.strictEqual(cache.get(), cache.get());
        assert.deepStrictEqual(cache.findByLabel("login").map((n) => n.id), ["login"]);
        assert.deepStrictEqual(cache.findByLabel(".login()").map((n) => n.id), ["login"]);
        fs.rmSync(file);
        assert.strictEqual(cache.getStats(), null);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });
});
