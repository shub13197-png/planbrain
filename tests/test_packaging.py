"""The packaged backend: its entry-point contract, and what the bundle excludes.

These run against the source tree. The packaged *artifact* is exercised in CI —
building it here would put seven minutes into every commit — but everything that
can be checked without a build is checked here, because the offline audit found
that a guarantee verified in the wrong environment is not verified.
"""

import ast
import os
import re
import tomllib
import json
import subprocess
import sys
import tempfile
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
    """Speak the protocol to a throwaway database.

    **Never the default.** With no `--db` the entry point opens the real
    per-user file named in `docs/install.md` -- the one holding a planner's
    imported history -- and the suite would be running against it. That was
    harmless only for as long as nothing committed; a request is now a
    transaction, so any writing method added here would land in it for real.
    """
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    with tempfile.TemporaryDirectory() as scratch:
        result = subprocess.run(
            [sys.executable, "-m", "planbrain.backend",
             "--db", str(Path(scratch) / "planning.db")],
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
        "BLOCK={'fugue','triad','pyarrow','adagio','sklearn',"
    "'formulaic','interface_meta','patsy','readline'}\n"
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
    # Wrapped by strip_orphaned once readline was excluded, so the assertion
    # is that strip_gpu is still in the chain rather than that it is the
    # whole of it.
    assert "strip_gpu(a.binaries)" in spec
    assert spec.count("a.binaries = ") == 1, "the filter chain was duplicated"
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


# --------------------------------------------------------------------------
# the project has to install on a machine that has never installed it
# --------------------------------------------------------------------------

def test_package_discovery_is_explicit_because_it_cannot_be_automatic():
    """`pip install -e ".[dev]"` could not work on a clean clone.

    setuptools' flat-layout auto-discovery refuses to build when it finds more
    than one candidate top-level directory, and this tree has four. The failure
    is hard, not a warning: "Multiple top-level packages discovered in a
    flat-layout".

    It went unnoticed for the life of the project because the development
    environment held an editable install from back when the tree had one
    top-level directory, and an install already in place is never re-resolved.
    The first line of the README, of `docs/install.md` and of every CI job was
    a command nobody could run. It surfaced on the first push to a real runner
    -- the same lesson as the packaged offline audit, which was verified in a
    checkout and turned out to differ in the environment that shipped.

    The second half of this test is the part that matters: it asserts the
    setting is *load-bearing* by confirming discovery would still be ambiguous
    without it. A config line that has quietly stopped doing anything is the
    shape this project keeps finding, so the guard checks the condition rather
    than the presence of the fix.
    """
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    find = config["tool"]["setuptools"]["packages"]["find"]
    assert "planbrain*" in find["include"], (
        "the shipped package must be named explicitly; auto-discovery cannot "
        "resolve this tree"
    )

    candidates = sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
        and (d / "__init__.py").exists()
    )
    assert len(candidates) > 1, (
        f"only {candidates} looks like a top-level package now. Auto-discovery "
        f"would succeed on its own, so this test no longer proves the explicit "
        f"declaration is doing anything -- check whether it still is."
    )


def test_the_build_backend_resolves_this_tree():
    """The call that actually failed, run directly.

    `get_requires_for_build_editable` is the first thing pip invokes and the
    step that raised. Running it here costs about a second and needs no network,
    which makes the real check cheap enough that CI is not the only place it
    happens.
    """
    from setuptools.build_meta import get_requires_for_build_editable

    root = Path(__file__).resolve().parents[1]
    previous = os.getcwd()
    os.chdir(root)
    try:
        get_requires_for_build_editable({})
    finally:
        os.chdir(previous)


def test_the_image_contains_everything_the_suite_reads():
    """The offline job runs the suite inside a container. It can only run the
    tests whose inputs were copied in.

    `tests/test_bundle_manifest.py` reads `packaging/`, which the Dockerfile did
    not copy, so that job failed at collection from the day the test was
    written. Nobody saw it, because there was no remote for CI to run on. The
    README meanwhile claimed the whole suite runs with no network interface.

    The tempting fix is to skip the tests whose inputs are missing. That is the
    worse one: it would make the claim true only of the tests that happened to
    be copied, which is the empty-result-reads-as-success shape with a green
    tick on it.

    So the rule asserted is the real one -- every repository path a test reads
    must exist inside the image -- rather than the fix.
    """
    root = Path(__file__).resolve().parents[1]

    copied = set()
    for line in (root / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            # `COPY a b ./dest` -- everything but the destination is a source.
            copied.update(line.split()[1:-1])

    referenced = set()
    for module in sorted((root / "tests").glob("test_*.py")):
        source = module.read_text(encoding="utf-8")
        for name in re.findall(r'(?:ROOT|root|parents\[1\])\s*/\s*"([^"/]+)"', source):
            referenced.add(name)

    missing = sorted(n for n in referenced if n not in copied and (root / n).exists())
    assert missing == [], (
        f"the suite reads {missing} but the Dockerfile does not copy them, so "
        f"the offline job cannot collect those tests"
    )
