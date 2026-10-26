"""Deterministic, LLM-free community names from contents: `label_communities_by_content`."""

from __future__ import annotations

import networkx as nx

from graphify.cluster import label_communities_by_content


def _node(G, nid, label, source_file="", loc="L1"):
    G.add_node(nid, label=label, source_file=source_file, source_location=loc, file_type="code")


def _auth_graph():
    G = nx.DiGraph()
    _node(G, "auth_file", "auth.py", "app/auth.py")
    _node(G, "login", "login()", "app/auth.py", "L10")
    _node(G, "hash", "hash_password()", "app/auth.py", "L4")
    _node(G, "session", "create_session()", "app/auth.py", "L20")
    _node(G, "path", "Path")  # imported library name: no source_file
    for target in ("hash", "session", "path"):
        G.add_edge("login", target, relation="calls")
    for member in ("login", "hash", "session"):
        G.add_edge("auth_file", member, relation="contains")
    for member in ("hash", "session"):
        G.add_edge(member, "path", relation="uses")
    return G


def test_names_by_dominant_file_and_most_connected_defined_symbol():
    G = _auth_graph()
    labels = label_communities_by_content(G, {0: ["auth_file", "login", "hash", "session", "path"]})
    # `Path` and the file node have more connections but don't describe the group.
    assert labels[0] == "app/auth.py · login"


def test_mixed_files_use_the_shared_folder():
    G = nx.DiGraph()
    for i, f in enumerate(["svc/billing/invoice.py", "svc/billing/tax.py", "svc/billing/refund.py"]):
        _node(G, f"n{i}", f"fn{i}()", f, "L3")
    G.add_edge("n0", "n1", relation="calls")
    G.add_edge("n0", "n2", relation="calls")
    assert label_communities_by_content(G, {3: ["n0", "n1", "n2"]})[3] == "svc/billing · fn0"


def test_symbol_repeating_the_file_name_collapses_to_the_file():
    G = nx.DiGraph()
    _node(G, "c", "Changelog", "CHANGELOG.md")
    _node(G, "s", "Section", "CHANGELOG.md", "L5")
    G.add_edge("c", "s", relation="contains")
    assert label_communities_by_content(G, {0: ["c", "s"]})[0] == "CHANGELOG.md"


def test_names_are_unique_and_deterministic():
    G = nx.DiGraph()
    for nid in ("a1", "a2", "b1", "b2"):
        _node(G, nid, "handler()", "api/views.py", "L1")
    G.add_edge("a1", "a2", relation="calls")
    G.add_edge("b1", "b2", relation="calls")
    comms = {0: ["a1", "a2"], 1: ["b1", "b2"]}
    first = label_communities_by_content(G, comms)
    assert len(set(first.values())) == 2
    assert first == label_communities_by_content(G, {1: ["b2", "b1"], 0: ["a2", "a1"]})


def test_missing_members_keep_the_placeholder_and_concepts_use_their_label():
    G = nx.Graph()
    G.add_node("idea", label="Retrieval", file_type="concept")
    labels = label_communities_by_content(G, {4: ["ghost"], 5: ["idea"]})
    assert labels == {4: "Community 4", 5: "Retrieval"}


def test_cluster_only_structural_labels_end_to_end(tmp_path):
    """`graphify cluster-only --structural-labels` writes names, never placeholders."""
    import json
    import subprocess
    import sys

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text(
        "def hash_password(p):\n    return p\n\n\ndef login(p):\n    return hash_password(p)\n",
        encoding="utf-8",
    )
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "graphify", *a], cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert run("extract", str(tmp_path), "--code-only", "--no-cluster").returncode == 0
    out = run("cluster-only", str(tmp_path), "--structural-labels")
    assert out.returncode == 0, out.stderr
    graph = json.loads((tmp_path / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    names = {n.get("community_name") for n in graph["nodes"] if n.get("community") is not None}
    assert names and not any(str(n).startswith("Community ") for n in names), names
    saved = json.loads((tmp_path / "graphify-out" / ".graphify_labels.json").read_text(encoding="utf-8"))
    assert set(saved.values()) == names
