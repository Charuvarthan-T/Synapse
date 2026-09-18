"""Semantic relations must survive code rebuilds (`graphify update` and `extract`)."""

from __future__ import annotations

import json

import networkx as nx
from networkx.readwrite import json_graph

from graphify.semantic_graph import (
    apply_semantic_relations,
    restore_semantic_edges,
    restore_semantic_layer,
)
from graphify.semantic_relations import SemanticExtractionResult, SemanticRelation
from graphify.watch import _rebuild_code


def _corpus(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "auth.py").write_text(
        "def hash_password(p):\n    return p[::-1]\n\n"
        "def login(p):\n    return hash_password(p)\n",
        encoding="utf-8",
    )
    (corpus / "db.py").write_text(
        "class Database:\n    def find_user(self, name):\n        return name\n",
        encoding="utf-8",
    )
    return corpus


def _load(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw, json_graph.node_link_graph(raw, edges="links")


def _node_id(G, label):
    return next(n for n, d in G.nodes(data=True) if d.get("label") == label)


def _enrich(graph_path):
    raw, G = _load(graph_path)
    login, hash_pw = _node_id(G, "login()"), _node_id(G, "hash_password()")
    database = _node_id(G, "Database")
    result = SemanticExtractionResult(
        relations=[
            # Onto an existing structural edge -> stored as a secondary relation.
            SemanticRelation(source=login, relation="validates", target=hash_pw, rationale="checks"),
            # Between unconnected nodes -> a new semantic edge.
            SemanticRelation(source=login, relation="related_to", target=database, rationale="reads"),
        ]
    )
    report = apply_semantic_relations(G, result)
    assert report.edges_augmented == 1 and report.edges_added == 1
    graph_path.write_text(
        json.dumps(json_graph.node_link_data(G, edges="links")), encoding="utf-8"
    )


def _semantic(raw):
    secondary = [
        (e["source"], e["target"], s["relation"])
        for e in raw["links"]
        for s in e.get("secondary_relations", [])
    ]
    added = [e["relation"] for e in raw["links"] if e.get("_origin") == "semantic_code"]
    return secondary, added


def test_update_keeps_semantic_relations(tmp_path):
    corpus = _corpus(tmp_path)
    assert _rebuild_code(corpus, acquire_lock=False) is True
    graph_path = corpus / "graphify-out" / "graph.json"
    _enrich(graph_path)
    before_secondary, before_added = _semantic(json.loads(graph_path.read_text(encoding="utf-8")))

    # Edit a file (adds a node) and rebuild, as `graphify update` does on save.
    with (corpus / "auth.py").open("a", encoding="utf-8") as fh:
        fh.write("\n\ndef logout(p):\n    return p\n")
    assert _rebuild_code(corpus, acquire_lock=False, force=True) is True

    raw, G = _load(graph_path)
    assert any(d.get("label") == "logout()" for _, d in G.nodes(data=True))
    after_secondary, after_added = _semantic(raw)
    assert [r for *_, r in after_secondary] == [r for *_, r in before_secondary] == ["validates"]
    assert after_added == before_added == ["related_to"]


def test_restore_semantic_edges_by_node_pair():
    previous = [
        {"source": "b", "target": "a", "relation": "calls",
         "secondary_relations": [{"relation": "validates"}]},
        {"source": "a", "target": "c", "relation": "uses",
         "secondary_relations": [{"relation": "manages", "_origin": "semantic_code"},
                                 {"relation": "handles"}]},
        {"source": "a", "target": "d", "relation": "related_to", "_origin": "semantic_code"},
        {"source": "a", "target": "gone", "relation": "calls",
         "secondary_relations": [{"relation": "validates"}]},
    ]
    fresh = [{"source": "a", "target": "b", "relation": "references"}]  # relation changed
    extra = restore_semantic_edges(previous, fresh, {"a", "b", "c", "d"})
    assert fresh[0]["secondary_relations"] == [{"relation": "validates"}]
    # a-c lost its structural edge: its annotations become a semantic edge.
    promoted = next(e for e in extra if {e["source"], e["target"]} == {"a", "c"})
    assert promoted["relation"] == "manages" and promoted["_origin"] == "semantic_code"
    assert promoted["secondary_relations"] == [{"relation": "handles"}]
    assert any(e["relation"] == "related_to" for e in extra)
    assert not any("gone" in (e["source"], e["target"]) for e in extra)
    fresh[0]["secondary_relations"][0]["relation"] = "mutated"
    assert previous[0]["secondary_relations"][0]["relation"] == "validates", "copied, not shared"


def test_extract_rebuild_keeps_semantic_relations(tmp_path):
    """`graphify extract` on an existing graph (build_merge) keeps the semantic layer."""
    import subprocess
    import sys

    corpus = _corpus(tmp_path)
    run = lambda: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "graphify", "extract", str(corpus), "--code-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    first = run()
    assert first.returncode == 0, first.stderr
    graph_path = corpus / "graphify-out" / "graph.json"
    _enrich(graph_path)

    with (corpus / "auth.py").open("a", encoding="utf-8") as fh:
        fh.write("\n\ndef logout(p):\n    return p\n")
    second = run()
    assert second.returncode == 0, second.stderr

    raw, G = _load(graph_path)
    assert any(d.get("label") == "logout()" for _, d in G.nodes(data=True))
    secondary, added = _semantic(raw)
    assert [r for *_, r in secondary] == ["validates"]
    assert added == ["related_to"]


def test_restore_semantic_layer_on_graph():
    G = nx.Graph()
    G.add_edge("a", "b", relation="references")  # relation changed on rebuild
    G.add_nodes_from(["c", "d"])
    previous = [
        {"source": "b", "target": "a", "relation": "calls",
         "secondary_relations": [{"relation": "validates"}]},
        {"source": "a", "target": "c", "relation": "handles", "_origin": "semantic_code"},
        {"source": "a", "target": "gone", "relation": "manages", "_origin": "semantic_code"},
        {"source": "a", "target": "b", "relation": "related_to", "_origin": "semantic_code"},
        {"source": "a", "target": "d", "relation": "uses",
         "secondary_relations": [{"relation": "manages"}]},
    ]
    assert restore_semantic_layer(G, previous) == 3
    assert G["a"]["b"]["secondary_relations"] == [{"relation": "validates"}]
    assert G["a"]["b"]["relation"] == "references", "structural edge is never replaced"
    assert G["a"]["c"]["relation"] == "handles"
    assert G["a"]["d"]["relation"] == "manages" and G["a"]["d"]["_origin"] == "semantic_code"
    assert "gone" not in G
