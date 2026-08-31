"""The packaged backend: its entry-point contract, and what the bundle excludes.

These run against the source tree. The packaged *artifact* is exercised in CI —
building it here would put seven minutes into every commit — but everything that
can be checked without a build is checked here, because the offline audit found
that a guarantee verified in the wrong environment is not verified.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "planbrain" / "backend" / "__main__.py"
SPEC = ROOT / "packaging" / "backend.spec"


# --------------------------------------------------------------------------
# engage() is first. Enforced, not requested.
# --------------------------------------------------------------------------

def test_engage_is_the_first_statement_of_the_entry_point():
    """docs/offline.md makes this the single wiring requirement for packaging.

    Asserted against the parsed source rather than trusted to a comment, because
    an import-time call-out is invisible to a guard engaged one line later, and
    a reordering during a merge would not fail anything else.
    """
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    # Drop the module docstring only. An earlier version of this test filtered
    # out every ast.Expr, which removed the engage() call it was looking for and
    # then asserted on what followed -- a check that could not pass for the right
    # reason.
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]

    first, second = body[0], body[1]
    assert isinstance(first, ast.ImportFrom), f"first statement is {type(first).__name__}"
    assert first.module == "planbrain.offline"
    assert [a.name for a in first.names] == ["engage"]

    assert isinstance(second, ast.Expr) and isinstance(second.value, ast.Call), (
        f"second statement is {type(second).__name__}, not the engage() call"
    )
    assert second.value.func.id == "engage"


def test_nothing_is_imported_before_engage_runs():
    """The stronger version: no import of any planning module can precede it."""
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    seen_engage_call = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "engage":
                seen_engage_call = True
                continue
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not seen_engage_call:
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert module == "planbrain.offline", (
                f"{module or names} is imported before engage() runs; an "
                f"import-time network call would not be caught"
            )
    assert seen_engage_call, "engage() is never called in the entry point"


# --------------------------------------------------------------------------
# the stdio protocol
# --------------------------------------------------------------------------

def _talk(requests):
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    result = subprocess.run(
        [sys.executable, "-m", "planbrain.backend"],
        input=payload, capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    return [json.loads(line) for line in result.stdout.strip().splitlines()], result


def test_the_handshake_reports_offline_before_any_request():
    """The shell shows this in its status bar, so it has to be true and early."""
    lines, _ = _talk([{"id": 1, "method": "shutdown"}])
    handshake = lines[0]
    assert handshake["ready"] is True
    assert handshake["offline"] is True
    assert handshake["protocol"] == 1
    assert "ping" in handshake["methods"]


def test_a_bad_method_is_an_error_response_not_a_crash():
    """A sidecar that dies on a bad request takes the app with it, and the shell
    cannot tell that from a crash."""
    lines, result = _talk([{"id": 1, "method": "nope"},
                           {"id": 2, "method": "ping"},
                           {"id": 3, "method": "shutdown"}])
    assert lines[1]["ok"] is False
    assert lines[1]["error"]["type"] == "UnknownMethod"
    assert lines[2]["ok"] is True, "the process survived the bad request"
    assert result.returncode == 0


def test_malformed_json_does_not_kill_the_pipe():
    payload = '{"id":1,"method":"ping"}\nnot json at all\n{"id":2,"method":"shutdown"}\n'
    result = subprocess.run(
        [sys.executable, "-m", "planbrain.backend"],
        input=payload, capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert any(l.get("error", {}).get("type") == "BadJSON" for l in lines)
    assert result.returncode == 0


def test_the_method_table_is_closed():
    """Not getattr on a module. A frontend bug -- or anything reaching the pipe --
    must not be able to call arbitrary code."""
    from planbrain.backend.api import METHODS

    assert all(callable(f) for f in METHODS.values())
    lines, _ = _talk([{"id": 1, "method": "api.session"},
                      {"id": 2, "method": "__import__"},
                      {"id": 3, "method": "shutdown"}])
    assert lines[1]["error"]["type"] == "UnknownMethod"
    assert lines[2]["error"]["type"] == "UnknownMethod"


def test_bad_params_are_distinguished_from_engine_failures():
    """A caller bug and a planning failure need different responses; a frontend
    that cannot tell them apart shows the wrong message."""
    lines, _ = _talk([{"id": 1, "method": "ping", "params": {"nonsense": 1}},
                      {"id": 2, "method": "shutdown"}])
    assert lines[1]["error"]["type"] == "BadParams"


# --------------------------------------------------------------------------
# what the bundle leaves out
# --------------------------------------------------------------------------

def test_the_pipeline_runs_without_the_excluded_packages():
    """The spec drops 83 MB of pyarrow/fugue, including arrow_flight -- an RPC
    library for moving data between machines, in an app that promises nothing
    leaves this one. This proves the exclusion is safe rather than hopeful."""
    code = (
        "import sys, importlib.abc\n"
        "BLOCK={'fugue','triad','pyarrow','adagio','sklearn'}\n"
        "class B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, n, p=None, t=None):\n"
        "        if n.split('.')[0] in BLOCK: raise ImportError(n)\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import sqlite3, pathlib\n"
        "from planbrain import forecast, netreq, rccp, simulate\n"
        "from planbrain.demo import build_demo, populate\n"
        "from planbrain.forecast import demand_keys\n"
        "c=sqlite3.connect(':memory:'); c.execute('PRAGMA foreign_keys=ON')\n"
        "c.executescript(pathlib.Path('planbrain/facts/schema.sql')"
        ".read_text(encoding='utf-8'))\n"
        "d=build_demo(seed=7); populate(c,d)\n"
        "k=demand_keys(d)[:4]\n"
        "forecast.run(c,d,keys=k); netreq.run(c,d,lot_sizing='cost_based')\n"
        "rccp.run(c,d); simulate.compare(c,d,keys=k,safety_days=7.0)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, timeout=300, cwd=ROOT)
    assert "OK" in result.stdout, result.stderr[-800:]


def test_the_spec_strips_gpu_artefacts():
    """Not load-bearing yet -- nothing links CUDA. In place before the model
    lands, because ggml-cuda plus its cuBLAS runtime is ~950 MB and would blow
    the committed budget in one commit."""
    spec = SPEC.read_text(encoding="utf-8")
    assert "def strip_gpu" in spec
    assert "a.binaries = strip_gpu(a.binaries)" in spec
    for token in ("cuda", "cublas", "cudnn"):
        assert token in spec


def test_the_spec_excludes_the_test_only_oracle():
    """stockpyl declares sphinx==4.5.0; it has no business in a shipped bundle."""
    assert '"stockpyl"' in SPEC.read_text(encoding="utf-8")


def test_the_size_budget_is_recorded_where_it_can_be_checked():
    """Committed in its own commit before the first build, per standing practice."""
    doc = (ROOT / "docs" / "packaging.md").read_text(encoding="utf-8")
    assert "400 MB" in doc
    assert "150 MB" in doc
