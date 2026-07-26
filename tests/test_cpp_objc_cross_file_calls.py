"""Cross-file member-call and include resolution for C++ (#1547) and ObjC (#1556).

Mirrors tests/test_swift_cross_file_calls.py. The principle under test is PRECISION
over recall: resolution is by RECEIVER TYPE (never a bare method name), guarded by a
single-definition god-node check — an ambiguous or uninferable receiver yields ZERO
edges rather than a fan-out.
"""

from __future__ import annotations

from pathlib import Path

from graphify.build import build_from_json
from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _label(result: dict, nid: str) -> str:
    for n in result["nodes"]:
        if n["id"] == nid:
            return n.get("label", "")
    return f"<{nid}>"


def _call_edges(result: dict, relations=("calls",)):
    """{(source_label, relation, target_label, confidence)} for the given relations."""
    out = set()
    for e in result["edges"]:
        if e.get("relation") in relations:
            out.add(
                (
                    _label(result, e["source"]),
                    e["relation"],
                    _label(result, e["target"]),
                    e.get("confidence"),
                )
            )
    return out


def test_cpp_cross_file_member_call_connects_with_relative_paths(tmp_path):
    """The headline #1547 fix: a paired class no longer islands — Main.cpp's use of
    Foo connects to Foo's method across files. Use RELATIVE input paths (the real
    `graphify extract .` usage), which is what exposes resolution gaps; an earlier
    absolute-path-only test masked them.

    NOTE: the file-level `#include` edge (Main.cpp file -> Foo.h file) is NOT asserted
    here. It relies on the extract() file-node id-remap, which `continue`s when the
    project `root` isn't symlink-resolved (e.g. macOS /var vs /private/var, worktrees),
    leaving the absolute-derived include target uncanonicalized. That's a known
    remaining gap tracked on #1547/#1556. The class connection below — the actual
    "connect with other classes" goal — resolves via the type-def index + the merged
    class and is robust to that gap.
    """
    import os

    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  void bar();\n};\n")
    _write(base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\n')
    _write(base / "Main.cpp", '#include "Foo.h"\nint main() { Foo f; f.bar(); return 0; }\n')
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = extract(
            [Path("src/Foo.h"), Path("src/Foo.cpp"), Path("src/Main.cpp")],
            cache_root=Path(".cache"),
            parallel=False,
        )
    finally:
        os.chdir(old)
    foo_classes = [n for n in result["nodes"] if n.get("label") == "Foo"]
    assert len(foo_classes) == 1, f"Foo should be one node, got {[n['id'] for n in foo_classes]}"
    labels = {n["id"]: n.get("label", "") for n in result["nodes"]}
    main_bar = [
        e
        for e in result["edges"]
        if e.get("relation") == "calls"
        and "main" in labels.get(e["source"], "")
        and e["target"].endswith("_bar")
    ]
    assert main_bar, "Main.cpp's f.bar() should resolve to Foo::bar across files"
    assert all("foo" in e["target"] for e in main_bar), main_bar


def test_cpp_instance_member_call_resolves(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  void bar();\n};\n")
    _write(base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\n')
    _write(base / "Main.cpp", '#include "Foo.h"\nint main() { Foo f; f.bar(); }\n')
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("main()", "calls", "bar", "INFERRED") in calls
    bar_calls = [c for c in calls if c[0] == "main()" and c[2] == "bar"]
    assert len(bar_calls) == 1


def test_cpp_pointer_member_call_resolves(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  void bar();\n};\n")
    _write(base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\n')
    _write(base / "Main.cpp", '#include "Foo.h"\nint main() { Foo* f = new Foo(); f->bar(); }\n')
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("main()", "calls", "bar", "INFERRED") in calls


def test_cpp_qualified_member_call_is_extracted(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  static void bar();\n};\n")
    _write(base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\n')
    _write(base / "Main.cpp", '#include "Foo.h"\nint main() { Foo::bar(); }\n')
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("main()", "calls", "bar", "EXTRACTED") in calls


def test_cpp_this_member_call_resolves_to_enclosing_class(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  void bar();\n  void baz();\n};\n")
    _write(
        base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\nvoid Foo::baz() { this->bar(); }\n'
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("baz", "calls", "bar", "EXTRACTED") in calls


def test_cpp_godnode_guard_ambiguous_and_unknown_receiver(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "A.h", "class A {\npublic:\n  void run();\n};\n")
    _write(base / "A.cpp", '#include "A.h"\nvoid A::run() {}\n')
    _write(base / "B.h", "class B {\npublic:\n  void run();\n};\n")
    _write(base / "B.cpp", '#include "B.h"\nvoid B::run() {}\n')
    _write(
        base / "Main.cpp", '#include "A.h"\n#include "B.h"\nint main() { x.run(); A a; a.run(); }\n'
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    src_by_id = {n["id"]: n.get("source_file") for n in result["nodes"]}
    run_calls = [
        e
        for e in result["edges"]
        if e.get("relation") == "calls"
        and _label(result, e["source"]) == "main()"
        and _label(result, e["target"]) == "run"
    ]
    assert len(run_calls) == 1
    assert Path(src_by_id[run_calls[0]["target"]]).name == "A.h"


def test_cpp_resolved_call_survives_build(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "class Foo {\npublic:\n  void bar();\n};\n")
    _write(base / "Foo.cpp", '#include "Foo.h"\nvoid Foo::bar() {}\n')
    _write(base / "Main.cpp", '#include "Foo.h"\nint main() { Foo f; f.bar(); }\n')
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    g = build_from_json(result)
    cross = [
        d
        for _, _, d in g.edges(data=True)
        if d.get("relation") == "calls" and d.get("confidence") == "INFERRED"
    ]
    assert len(cross) >= 1


def test_cpp_unknown_receiver_emits_no_edge(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Helper.h", "class Helper {\npublic:\n  void help();\n};\n")
    _write(base / "Helper.cpp", '#include "Helper.h"\nvoid Helper::help() {}\n')
    _write(base / "Main.cpp", '#include "Helper.h"\nint main() { mystery.help(); }\n')
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert not any(c[0] == "main()" and c[2] == "help" for c in calls)


def test_objc_instance_message_send_resolves(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "@interface Foo : NSObject\n- (void)doThing;\n@end\n")
    _write(base / "Foo.m", '#import "Foo.h"\n@implementation Foo\n- (void)doThing {}\n@end\n')
    _write(
        base / "Bar.m",
        '#import "Foo.h"\n@implementation Bar\n'
        "- (void)go {\n  Foo *f = [[Foo alloc] init];\n  [f doThing];\n}\n@end\n",
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("-go", "calls", "-doThing", "INFERRED") in calls


def test_objc_self_message_send_resolves_to_enclosing_class(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "@interface Foo : NSObject\n- (void)render;\n- (void)setup;\n@end\n")
    _write(
        base / "Foo.m",
        '#import "Foo.h"\n@implementation Foo\n'
        "- (void)setup { [self render]; }\n- (void)render {}\n@end\n",
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    calls = _call_edges(result)
    assert ("-setup", "calls", "-render", "EXTRACTED") in calls


def test_objc_godnode_guard_ambiguous_selector(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "A.h", "@interface A : NSObject\n- (void)doStuff;\n@end\n")
    _write(base / "A.m", '#import "A.h"\n@implementation A\n- (void)doStuff {}\n@end\n')
    _write(base / "B.h", "@interface B : NSObject\n- (void)doStuff;\n@end\n")
    _write(base / "B.m", '#import "B.h"\n@implementation B\n- (void)doStuff {}\n@end\n')
    _write(
        base / "C.m",
        '#import "A.h"\n#import "B.h"\n@implementation C\n- (void)go { [thing doStuff]; }\n@end\n',
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    go_calls = [
        e
        for e in result["edges"]
        if e.get("relation") == "calls" and _label(result, e["source"]) == "-go"
    ]
    assert go_calls == []


def test_objc_resolved_calls_survive_build(tmp_path: Path):
    base = tmp_path / "src"
    _write(base / "Foo.h", "@interface Foo : NSObject\n- (void)doThing;\n@end\n")
    _write(base / "Foo.m", '#import "Foo.h"\n@implementation Foo\n- (void)doThing {}\n@end\n')
    _write(
        base / "Bar.m",
        '#import "Foo.h"\n@implementation Bar\n'
        "- (void)go {\n  Foo *f = [[Foo alloc] init];\n  [f doThing];\n}\n@end\n",
    )
    result = extract(sorted(base.glob("*")), cache_root=tmp_path / "cache")

    g = build_from_json(result)
    cross = [
        d
        for _, _, d in g.edges(data=True)
        if d.get("relation") == "calls" and d.get("confidence") == "INFERRED"
    ]
    assert len(cross) >= 1
