"""#1656 — word counts are cached against each file's stat signature so
detect() doesn't re-parse every unchanged PDF/docx on each run just to size
the corpus.
"""

from __future__ import annotations

from pathlib import Path

from graphify import cache


def test_word_count_cached_until_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    f = tmp_path / "doc.txt"
    f.write_text("one two three four five")

    calls = {"n": 0}

    def compute(p: Path) -> int:
        calls["n"] += 1
        return len(p.read_text().split())

    assert cache.cached_word_count(f, tmp_path, compute) == 5
    assert calls["n"] == 1
    assert cache.cached_word_count(f, tmp_path, compute) == 5
    assert calls["n"] == 1

    f.write_text("only three words now")
    assert cache.cached_word_count(f, tmp_path, compute) == 4
    assert calls["n"] == 2


def test_word_count_augments_existing_hash_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    f = tmp_path / "m.py"
    f.write_text("x = 1\n")
    h = cache.file_hash(f, tmp_path)
    assert h
    wc = cache.cached_word_count(f, tmp_path, lambda p: len(p.read_text().split()))
    assert wc == 3
    assert cache.file_hash(f, tmp_path) == h
    key = str(cache._normalize_path(f).resolve())
    entry = cache._stat_index[key]
    assert entry.get("hashes", {}).get("m.py") == h and entry.get("word_count") == 3


def test_file_hash_is_order_independent_across_roots(tmp_path, monkeypatch):
    """#1989: the stat-index memo must be keyed by the salt (path relative to
    root) that enters the digest, so the same (file, root) returns the same
    digest regardless of what root was hashed first."""
    import hashlib
    from graphify import cache

    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    root_a = tmp_path / "a"
    root_a.mkdir()
    f = root_a / "doc.txt"
    f.write_text("hello world\n")
    root_b = tmp_path / "b"
    root_b.mkdir()

    content = f.read_bytes()
    exp_rel = hashlib.sha256(content + b"\x00" + b"doc.txt").hexdigest()
    exp_abs = hashlib.sha256(
        content
        + b"\x00"
        + str(cache._normalize_path(f).resolve()).replace("\\", "/").lower().encode()
    ).hexdigest()

    assert cache.file_hash(f, root_a) == exp_rel
    assert cache.file_hash(f, root_b) == exp_abs
    assert cache.file_hash(f, root_a) == exp_rel

    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)
    assert cache.file_hash(f, root_b) == exp_abs
    assert cache.file_hash(f, root_a) == exp_rel


def test_file_hash_ignores_legacy_unsalted_entry(tmp_path, monkeypatch):
    """A pre-#1989 entry carrying a bare "hash" (no salt) is never trusted."""
    import hashlib
    from graphify import cache

    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)
    f = tmp_path / "m.py"
    f.write_text("x = 1\n")
    st = f.stat()
    key = str(cache._normalize_path(f).resolve())
    cache._stat_index[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": "deadbeef"}
    exp = hashlib.sha256(f.read_bytes() + b"\x00" + b"m.py").hexdigest()
    assert cache.file_hash(f, tmp_path) == exp
    entry = cache._stat_index[key]
    assert "hash" not in entry and entry["hashes"]["m.py"] == exp
