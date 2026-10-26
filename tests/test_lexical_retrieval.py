"""Hybrid retrieval: BM25 over symbol source code fused with graph ranking."""

from __future__ import annotations

import networkx as nx

from graphify.lexical_retrieval import bm25_scores, code_tokens, lexical_ranking, rrf_fuse
from graphify.serve import _query_graph_text

AUTH_PY = '''def hash_password(password):
    return sha256(password)


def login(db, name, password):
    user = db.find_user(name)
    return user.password_hash == hash_password(password)


def logout(session):
    session.clear()
'''

STORE_PY = '''def find_user(name):
    return USERS.get(name)


def save_token(token):
    TOKENS.append(token)
'''


def _repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text(AUTH_PY, encoding="utf-8")
    (tmp_path / "app" / "store.py").write_text(STORE_PY, encoding="utf-8")
    return tmp_path


def _graph() -> nx.DiGraph:
    G = nx.DiGraph()
    for nid, label, src, loc in [
        ("hash_password", "hash_password()", "app/auth.py", "L1"),
        ("login", "login()", "app/auth.py", "L5"),
        ("logout", "logout()", "app/auth.py", "L10"),
        ("find_user", "find_user()", "app/store.py", "L1"),
        ("save_token", "save_token()", "app/store.py", "L5"),
    ]:
        G.add_node(nid, label=label, source_file=src, source_location=loc, file_type="code")
    G.add_edge("login", "hash_password", relation="calls", confidence="EXTRACTED")
    G.add_edge("login", "find_user", relation="calls", confidence="EXTRACTED")
    return G


def test_code_tokens_split_identifiers():
    tokens = code_tokens("user.password_hash = makeClient(x)")
    assert {"user", "password_hash", "password", "hash", "makeclient", "make", "client"} <= set(tokens)
    assert "x" not in tokens  # single characters are noise


def test_bm25_prefers_documents_with_rare_query_terms():
    docs = {"a": ["token", "save", "token"], "b": ["user", "name"], "c": ["user", "token"]}
    scores = bm25_scores(docs, ["token"])
    assert set(scores) == {"a", "c"}
    assert scores["a"] > scores["c"]
    assert bm25_scores(docs, []) == {}


def test_lexical_ranking_reads_symbol_bodies(tmp_path):
    root = _repo(tmp_path)
    ranked = lexical_ranking(_graph(), "session.clear()", root)
    assert ranked[0] == "logout"  # only logout's body mentions session/clear


def test_rrf_fuse_rewards_agreement_and_keeps_both_lists():
    fused = rrf_fuse(["a", "b", "c"], ["c", "d"])
    assert fused[0] == "c"  # ranked by both
    assert set(fused) == {"a", "b", "c", "d"}


def test_hybrid_query_surfaces_code_only_matches(tmp_path):
    root = _repo(tmp_path)
    G = _graph()
    plain = _query_graph_text(G, "login TOKENS", depth=1)
    hybrid = _query_graph_text(G, "login TOKENS", depth=1, hybrid=True, root=root)
    assert "save_token()" not in plain  # no edge reaches it from login
    assert "save_token()" in hybrid  # its body uses TOKENS
    assert "Hybrid lexical" in hybrid


def test_hybrid_query_without_name_match_falls_back_to_code(tmp_path):
    root = _repo(tmp_path)
    G = _graph()
    assert _query_graph_text(G, "USERS", depth=1) == "No matching nodes found."
    text = _query_graph_text(G, "USERS", depth=1, hybrid=True, root=root)
    assert "find_user()" in text


def test_hybrid_without_root_ranks_like_plain_query():
    G = _graph()

    def node_lines(text):
        return [line for line in text.splitlines() if line.startswith("NODE ")]

    assert node_lines(_query_graph_text(G, "login", depth=1, hybrid=True)) == node_lines(
        _query_graph_text(G, "login", depth=1)
    )
