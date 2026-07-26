"""Indirect dispatch edges.

A function passed BY NAME as a call argument (`executor.submit(fn)`, `Thread(target=fn)`) is a
real dependency, but the callee-only call scan never recorded it — so `affected` (blast radius)
dropped those callers. These tests pin that such calls now emit a distinct `indirect_call` edge
(leaving the precise `calls` relation untouched) and that `affected` picks them up.

They also pin the soundness guards: an argument that is a PARAMETER or a LOCAL binding of the
enclosing function is a local value, not the module-level function it shares a name with, and a
non-callable same-named node is never a dispatch target — neither may manufacture an edge.
"""

import os
from pathlib import Path

import networkx as nx

from graphify.affected import affected_nodes
from graphify.extract import extract, extract_python

SRC = """\
import threading


def handler(x):
    return x * 2


def direct():
    return handler(1)                          # direct call -> `calls`


def via_submit(pool):
    pool.submit(handler, 1)                    # indirect: positional arg


def via_thread():
    threading.Thread(target=handler).start()   # indirect: keyword arg


def via_map(xs):
    return map(handler, xs)                    # indirect: map(fn, xs)
"""


def _build(tmp_path):
    (tmp_path / "dispatch.py").write_text(SRC)
    r = extract_python(tmp_path / "dispatch.py")
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    return r, nid


def _rels(r, relation):
    return {(e["source"], e["target"]) for e in r["edges"] if e["relation"] == relation}


def test_emits_indirect_call_edges_and_keeps_calls_precise(tmp_path):
    r, nid = _build(tmp_path)
    calls = _rels(r, "calls")
    indirect = _rels(r, "indirect_call")
    handler = nid["handler"]

    assert (nid["direct"], handler) in calls
    assert (nid["via_submit"], handler) in indirect
    assert (nid["via_thread"], handler) in indirect
    assert (nid["via_map"], handler) in indirect
    assert (nid["via_submit"], handler) not in calls
    assert (nid["via_thread"], handler) not in calls
    assert (nid["via_map"], handler) not in calls

    for e in (e for e in r["edges"] if e["relation"] == "indirect_call"):
        assert e["context"] == "argument" and e["confidence"] == "INFERRED"


def test_affected_includes_indirect_callers(tmp_path):
    r, nid = _build(tmp_path)
    g = nx.DiGraph()
    for n in r["nodes"]:
        g.add_node(n["id"], **n)
    for e in r["edges"]:
        g.add_edge(e["source"], e["target"], **e)

    affected = {h.node_id for h in affected_nodes(g, nid["handler"])}
    assert nid["via_submit"] in affected
    assert nid["via_thread"] in affected
    assert nid["via_map"] in affected


def _extract(tmp_path, src):
    (tmp_path / "m.py").write_text(src)
    r = extract_python(tmp_path / "m.py")
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    return r, nid


PARAM_SHADOW = """\
def handler():
    return 1


def via(pool, handler):
    pool.submit(handler)        # `handler` is a PARAMETER, not the module fn
"""


def test_param_shadow_emits_no_indirect_call(tmp_path):
    r, nid = _extract(tmp_path, PARAM_SHADOW)
    indirect = _rels(r, "indirect_call")
    assert (nid["via"], nid["handler"]) not in indirect
    assert all(t != nid["handler"] for _s, t in indirect)


LOCAL_SHADOW = """\
def handler():
    return 1


def make():
    return lambda: None


def via(pool):
    handler = make()            # `handler` is a LOCAL binding, not the module fn
    pool.submit(handler)
"""


def test_local_assignment_shadow_emits_no_indirect_call(tmp_path):
    r, nid = _extract(tmp_path, LOCAL_SHADOW)
    indirect = _rels(r, "indirect_call")
    assert (nid["via"], nid["handler"]) not in indirect
    assert all(t != nid["handler"] for _s, t in indirect)


DATA_VAR = """\
def config():
    return {"k": "v"}


def process(x):
    return x


def use():
    config = {"k": "v"}         # local DATA var that happens to match `config()`
    process(config)             # arg resolves to a non-callable local, not the fn
"""


def test_data_var_matching_function_name_emits_no_indirect_call(tmp_path):
    r, nid = _extract(tmp_path, DATA_VAR)
    indirect = _rels(r, "indirect_call")
    assert (nid["use"], nid["config"]) not in indirect


REAL_PASS = """\
def handler():
    return 1


def via(pool):
    pool.submit(handler)        # genuine module-level fn passed by name
"""


def test_genuine_module_function_still_emits_indirect_call(tmp_path):
    """No recall regression: a real module fn passed by name still emits an edge."""
    r, nid = _extract(tmp_path, REAL_PASS)
    indirect = _rels(r, "indirect_call")
    assert (nid["via"], nid["handler"]) in indirect


def _extract_dir(tmp_path, files: dict[str, str]):
    base = tmp_path / "pkg"
    base.mkdir()
    for name, body in files.items():
        (base / name).write_text(body)
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        r = extract(
            [Path("pkg") / name for name in files],
            cache_root=Path(".cache"),
            parallel=False,
        )
    finally:
        os.chdir(old)
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    return r, nid


def test_cross_file_indirect_survives_id_relativization(tmp_path):
    """Regression: when the scan root relativizes node ids (cache_root == project
    root, as the `graphify extract` CLI passes), the id-remap rewrites node ids
    AFTER per-file extraction. The cross-file indirect callable guard must read
    callable-ness from a node marker that survives the remap, not a stale pre-remap
    id set — otherwise every cross-file indirect_call is silently dropped (only
    in-file ones survive). This is the exact shape the CLI hit."""
    base = tmp_path / "proj"
    (base / "handlers").mkdir(parents=True)
    (base / "handlers" / "__init__.py").write_text("def on_event(x):\n    return x\n")
    (base / "scheduler.py").write_text(
        "from handlers import on_event\n\n\ndef schedule(pool):\n    pool.submit(on_event)\n"
    )
    old = os.getcwd()
    try:
        os.chdir(base)
        r = extract(
            [Path("handlers/__init__.py"), Path("scheduler.py")], cache_root=base, parallel=False
        )
    finally:
        os.chdir(old)
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    assert (nid["schedule"], nid["on_event"]) in _rels(r, "indirect_call")
    assert not any("_callable" in n for n in r["nodes"])


def test_cross_file_imported_callback_emits_indirect_call(tmp_path):
    r, nid = _extract_dir(
        tmp_path,
        {
            "handlers.py": "def on_event(x):\n    return x\n",
            "scheduler.py": (
                "from handlers import on_event\n\n\n"
                "def schedule(pool):\n"
                "    pool.submit(on_event)\n"
            ),
        },
    )
    indirect = _rels(r, "indirect_call")
    calls = _rels(r, "calls")
    assert (nid["schedule"], nid["on_event"]) in indirect
    assert (nid["schedule"], nid["on_event"]) not in calls
    for e in r["edges"]:
        if e["relation"] == "indirect_call" and e["target"] == nid["on_event"]:
            assert e["confidence"] == "INFERRED"


def test_cross_file_class_ref_is_not_indirect_call(tmp_path):
    """The cross-file resolver guard in extract.py must suppress indirect_call edges
    whose target is a class imported from another module (a descriptor, not an
    invocation), while a genuine imported function callback still emits its edge.
    This exercises the _callable_class exclusion on the CROSS-FILE path, which the
    intra-file test (test_class_ref_is_not_indirect_call) does not reach."""
    r, nid = _extract_dir(
        tmp_path,
        {
            "models.py": ("class Widget:\n    pass\n\n\ndef on_event(x):\n    return x\n"),
            "scheduler.py": (
                "from models import Widget, on_event\n\n\n"
                "def schedule(pool, db, i):\n"
                "    db.get(Widget, i)          # imported class as descriptor -> no edge\n"
                "    pool.submit(on_event)      # imported fn callback -> indirect_call\n"
            ),
        },
    )
    indirect = _rels(r, "indirect_call")
    assert all(t != nid["Widget"] for _s, t in indirect)
    assert (nid["schedule"], nid["on_event"]) in indirect


def test_cross_file_affected_includes_importing_dispatcher(tmp_path):
    r, nid = _extract_dir(
        tmp_path,
        {
            "handlers.py": "def on_event(x):\n    return x\n",
            "scheduler.py": (
                "from handlers import on_event\n\n\n"
                "def schedule(pool):\n"
                "    pool.submit(on_event)\n"
            ),
        },
    )
    g = nx.DiGraph()
    for n in r["nodes"]:
        g.add_node(n["id"], **n)
    for e in r["edges"]:
        g.add_edge(e["source"], e["target"], **e)
    affected = {h.node_id for h in affected_nodes(g, nid["on_event"])}
    assert nid["schedule"] in affected


def test_cross_file_param_shadow_emits_no_indirect_call(tmp_path):
    """Soundness carries across files: an imported name shadowed by a parameter
    is the local value, so no cross-file indirect edge is manufactured."""
    r, nid = _extract_dir(
        tmp_path,
        {
            "handlers.py": "def on_event(x):\n    return x\n",
            "scheduler.py": (
                "from handlers import on_event\n\n\n"
                "def schedule(pool, on_event):\n"
                "    pool.submit(on_event)\n"
            ),
        },
    )
    indirect = _rels(r, "indirect_call")
    assert (nid["schedule"], nid["on_event"]) not in indirect


def test_module_level_dict_registry_emits_indirect_call(tmp_path):
    r, nid = _extract(
        tmp_path,
        (
            "def create(x):\n    return x\n\n\n"
            "def delete(x):\n    return x\n\n\n"
            'ROUTES = {"create": create, "delete": delete}\n'
        ),
    )
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "m.py")
    assert (file_nid, nid["create"]) in indirect
    assert (file_nid, nid["delete"]) in indirect
    assert (file_nid, nid["create"]) not in _rels(r, "calls")


def test_module_level_list_registry_emits_indirect_call(tmp_path):
    r, nid = _extract(
        tmp_path,
        (
            "def on_start():\n    pass\n\n\n"
            "def on_stop():\n    pass\n\n\n"
            "HOOKS = [on_start, on_stop]\n"
        ),
    )
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "m.py")
    assert (file_nid, nid["on_start"]) in indirect
    assert (file_nid, nid["on_stop"]) in indirect


def test_function_scoped_dispatch_table_attributes_to_function(tmp_path):
    r, nid = _extract(
        tmp_path, ('def cb(x):\n    return x\n\n\ndef build():\n    return {"k": cb}\n')
    )
    assert (nid["build"], nid["cb"]) in _rels(r, "indirect_call")


def test_dict_keys_are_not_dispatch_targets(tmp_path):
    """Only VALUES are references; a function used as a dict KEY is not invoked
    through the table and must not produce an edge."""
    r, nid = _extract(
        tmp_path, ("def keyfn():\n    pass\n\n\ndef valfn():\n    pass\n\n\nT = {keyfn: valfn}\n")
    )
    indirect = _rels(r, "indirect_call")
    assert all(t != nid["keyfn"] for _s, t in indirect)
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "m.py")
    assert (file_nid, nid["valfn"]) in indirect


def test_non_callable_collection_value_emits_no_indirect_call(tmp_path):
    """A data value in the table (a number, a string) is not a callable and must
    never become a dispatch target."""
    r, nid = _extract(tmp_path, ('def use():\n    pass\n\n\nCONF = {"timeout": 30, "name": use}\n'))
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "m.py")
    assert (file_nid, nid["use"]) in indirect
    assert len(indirect) == 1


def test_module_level_reassigned_name_shadows_dispatch_value(tmp_path):
    """If the name is rebound to data at module scope, the table value is that
    data, not the same-named function — no edge."""
    r, nid = _extract(
        tmp_path, ('def handler():\n    pass\n\n\nhandler = object()\nT = {"h": handler}\n')
    )
    indirect = _rels(r, "indirect_call")
    assert all(t != nid["handler"] for _s, t in indirect)


def test_cross_file_dict_registry_emits_indirect_call(tmp_path):
    r, nid = _extract_dir(
        tmp_path,
        {
            "handlers.py": "def on_event(x):\n    return x\n",
            "registry.py": ('from handlers import on_event\n\n\nROUTES = {"event": on_event}\n'),
        },
    )
    indirect = _rels(r, "indirect_call")
    reg_file = next(n["id"] for n in r["nodes"] if n["label"] == "registry.py")
    assert (reg_file, nid["on_event"]) in indirect


def _extract_js_dir(tmp_path, files: dict[str, str]):
    base = tmp_path / "src"
    base.mkdir()
    for name, body in files.items():
        (base / name).write_text(body)
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        r = extract(
            [Path("src") / name for name in files],
            cache_root=Path(".cache"),
            parallel=False,
        )
    finally:
        os.chdir(old)
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    return r, nid


def test_js_function_scoped_call_argument(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "a.js": (
                "function handler(x){ return x; }\nfunction via(pool){ pool.submit(handler); }\n"
            )
        },
    )
    indirect = _rels(r, "indirect_call")
    assert (nid["via"], nid["handler"]) in indirect
    assert (nid["via"], nid["handler"]) not in _rels(r, "calls")


def test_js_module_object_and_array_registry(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "a.js": (
                "function handler(x){ return x; }\n"
                "const cb = () => {};\n"
                "const ROUTES = { create: handler, run: cb };\n"
                "const HOOKS = [handler, cb];\n"
            )
        },
    )
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "a.js")
    assert (file_nid, nid["handler"]) in indirect
    assert (file_nid, nid["cb"]) in indirect


def test_js_module_level_callback_registration(tmp_path):
    """Express routes / event wiring / timers live at module scope in JS."""
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "r.js": (
                "function home(req, res){}\n"
                "const list = () => {};\n"
                'app.get("/", home);\n'
                'emitter.on("evt", list);\n'
                "setTimeout(home, 100);\n"
            )
        },
    )
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "r.js")
    assert (file_nid, nid["home"]) in indirect
    assert (file_nid, nid["list"]) in indirect


def test_js_inline_arrow_argument_is_not_a_reference(tmp_path):
    """An inline arrow / function expression is a direct definition, not a
    by-name reference — it must not emit an indirect_call."""
    r, nid = _extract_js_dir(
        tmp_path,
        {"i.js": ("function via(arr){ arr.map(x => x * 2); arr.forEach(function(y){}); }\n")},
    )
    assert _rels(r, "indirect_call") == set()


def test_js_parameter_shadow_emits_no_indirect_call(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {"s.js": ("function handler(){}\nfunction via(pool, handler){ pool.submit(handler); }\n")},
    )
    indirect = _rels(r, "indirect_call")
    assert all(t != nid["handler"] for _s, t in indirect)


def test_js_object_keys_and_data_values_excluded(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "k.js": (
                "function keyfn(){}\n"
                "function valfn(){}\n"
                "const T = { [keyfn]: valfn, timeout: 30 };\n"
            )
        },
    )
    indirect = _rels(r, "indirect_call")
    assert all(t != nid["keyfn"] for _s, t in indirect)
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "k.js")
    assert (file_nid, nid["valfn"]) in indirect


def test_js_shorthand_property_reference(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path, {"sh.js": ("function handler(){}\nconst obj = { handler };\n")}
    )
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "sh.js")
    assert (file_nid, nid["handler"]) in _rels(r, "indirect_call")


def test_js_cross_file_imported_callback_in_object(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "h.js": "export function onEvent(x){ return x; }\n",
            "reg.js": ('import { onEvent } from "./h.js";\nconst ROUTES = { e: onEvent };\n'),
        },
    )
    indirect = _rels(r, "indirect_call")
    reg_file = next(n["id"] for n in r["nodes"] if n["label"] == "reg.js")
    assert (reg_file, nid["onEvent"]) in indirect


def test_typescript_typed_params_and_arrow_consts(tmp_path):
    r, nid = _extract_js_dir(
        tmp_path,
        {
            "t.ts": (
                "function handler(x: number): number { return x; }\n"
                "const cb = (): void => {};\n"
                "function via(pool: Pool): void { pool.submit(handler); }\n"
                "const ROUTES: Record<string, unknown> = { create: handler, run: cb };\n"
            )
        },
    )
    indirect = _rels(r, "indirect_call")
    file_nid = next(n["id"] for n in r["nodes"] if n["label"] == "t.ts")
    assert (nid["via"], nid["handler"]) in indirect
    assert (file_nid, nid["handler"]) in indirect
    assert (file_nid, nid["cb"]) in indirect


def test_class_ref_is_not_indirect_call(tmp_path):
    """A class referenced BY NAME as a value is a descriptor, not an invocation, so it
    must NOT emit an indirect_call edge — across all three symptom families of #2137:
    ORM args (`select(Model)`, `db.get(Model, id)`), exception tuples
    (`except (ErrorA, ErrorB)`), and string-literal getattr resolving to a same-named
    class. A real function callback in the same file still emits its edge."""
    src = (
        "class ErrorA(Exception):\n    pass\n"
        "class ErrorB(Exception):\n    pass\n"
        "class KbArticle:\n    pass\n"
        "\n"
        "def handler(x):\n    return x\n"
        "\n"
        "def use_except():\n"
        "    try:\n        pass\n"
        "    except (ErrorA, ErrorB) as e:   # exception tuple -> classes, not calls\n"
        "        return e\n"
        "\n"
        "def use_getattr(run):\n"
        '    return getattr(run, "KbArticle", 0)   # literal resolving to a class\n'
        "\n"
        "def use_orm(db, i):\n"
        "    db.get(KbArticle, i)            # class as descriptor\n"
        "    return select(KbArticle)        # class as descriptor\n"
        "\n"
        "def register(pool):\n"
        "    pool.submit(handler)            # real fn callback -> indirect_call\n"
    )
    (tmp_path / "orm.py").write_text(src)
    r = extract_python(tmp_path / "orm.py")
    nid = {n["label"].rstrip("()"): n["id"] for n in r["nodes"]}
    indirect = _rels(r, "indirect_call")
    class_ids = {nid["ErrorA"], nid["ErrorB"], nid["KbArticle"]}
    assert not any(t in class_ids for _, t in indirect)
    assert (nid["register"], nid["handler"]) in indirect
