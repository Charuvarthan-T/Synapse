import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { loadGraph, degreeByNode, topGodNodes, groupByCommunity, GraphData } from "../../src/graphModel";

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
});
