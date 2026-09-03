# PyInstaller spec for the Planning Brain backend sidecar.
#
#     pyinstaller packaging/backend.spec --noconfirm
#
# One-dir, not one-file. One-file unpacks to a temp directory on every launch,
# which costs seconds on a cold start and — more to the point — writes a copy of
# the whole application somewhere the user did not choose, in a product whose
# promise is that nothing leaves the machine. One-dir also lets the Tauri
# bundler treat the tree as a resource directory.
#
# Two techniques taken from GGUFloader/gguf-loader, which solved this for
# llama-cpp-python first:
#   * filter native artefacts by filename rather than trusting the hook
#   * collect_submodules() for anything imported lazily, which static analysis
#     misses and which fails only at runtime on a customer's machine
#
# Its third answer does NOT transfer: it ships CPU inference and downloads the
# CUDA build in-app. That is a runtime fetch, which our offline guarantee
# forbids. See docs/packaging.md.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

# Lazily imported at runtime, so the analyser cannot see them from the source.
# statsforecast reaches into scipy.stats and statsmodels' tsa tree only when a
# model is actually fitted, which is well after import.
hiddenimports = [
    "planbrain.backend.api",
    "planbrain.mapping",
    "openpyxl",
    "yaml",
    *collect_submodules("statsforecast"),
    *collect_submodules("utilsforecast"),
    "scipy.stats",
    "scipy.special",
    "scipy._lib.array_api_compat",
    "statsmodels.tsa",
    "statsmodels.tsa.statespace",
    "jsonschema_specifications",
]

datas = [
    # The schema is read at runtime with read_text(); PyInstaller has no way to
    # know that from the source.
    (str(ROOT / "planbrain" / "facts" / "schema.sql"), "planbrain/facts"),
    (str(ROOT / "planbrain" / "contracts" / "solver_io.schema.json"),
     "planbrain/contracts"),
    # Draft metaschemas, shipped as package data rather than code.
    *collect_data_files("jsonschema_specifications"),
    # Shipped mapping profiles are data files read at runtime, so the analyser
    # cannot see them from the source.
    (str(ROOT / "planbrain" / "mapping" / "profiles"), "planbrain/mapping/profiles"),
]

# Nothing here is used at runtime. Dropped by name because a hook that happens
# to pull one in is a silent 100 MB, and nobody notices bundle bloat until a
# download fails on a rural connection -- the exact deployment this targets.
excludes = [
    "tkinter", "turtle", "idlelib",
    "matplotlib", "PIL", "IPython", "notebook", "jupyter_core",
    "pytest", "_pytest", "sphinx", "docutils", "setuptools", "pip",
    "stockpyl",           # test-only oracle; see docs/decisions.md
    # statsforecast pulls fugue for distributed execution, which drags in
    # triad and pyarrow: 83 MB, of which 14.7 MB is arrow_flight -- an RPC
    # library for moving data BETWEEN MACHINES, in an application whose
    # promise is that nothing leaves this one. We never run distributed, and
    # test_packaging asserts the pipeline still works without them.
    "fugue", "triad", "adagio", "pyarrow", "fsspec",
    "sklearn", "scipy.optimize._trlib", "scipy.sparse.csgraph",
    # TLS. Found by the bundle manifest gate, not by the size budget, which is
    # the point of having the gate: 6.2 MB of OpenSSL sitting inside an
    # application whose central promise is that nothing leaves the machine. It
    # is never imported at runtime -- the guard would block any socket using it
    # anyway -- so shipping it is dead weight AND a confusing thing to find in
    # the bundle of a product that claims to be offline.
    "ssl", "_ssl", "urllib.request", "http.client",
    # BeautifulSoup and friends arrive through pandas.read_html -- an HTML
    # scraping stack in a planning sidecar. Pure Python, so they were invisible
    # to the gate until it learned to read the PYZ archive.
    "bs4", "html5lib", "soupsieve", "webencodings", "lxml",
    # IPython display machinery, pulled by pandas' rich repr. Nothing here has
    # a notebook to render into.
    "IPython", "pygments", "traitlets",
    # requests / template machinery, pulled by a hook rather than by us.
    "charset_normalizer", "certifi", "idna", "urllib3", "requests",
    "jinja2", "markupsafe", "psutil",
    # NOT "openpyxl.chart": excluding a submodule breaks the parent package,
    # because openpyxl imports chart from its own workbook module. That
    # exclusion shipped from item 12 until the bundle identity gate flagged
    # openpyxl as allowlisted-but-absent, and the packaged app failed on the
    # first .xlsx anyone opened -- the mapping UI's primary use case.
    "numpy.f2py", "scipy.io.matlab",
    "pandas.tests", "numpy.tests", "scipy.tests", "statsmodels.tests",
    # statsmodels' formula API -- `y ~ x + z` strings. We never touch it:
    # AutoETS reaches statsmodels through statsforecast's model classes, not
    # through a formula. `patsy` was allowlisted for months on the assumption
    # that statsmodels needed it; blocking all four and running the whole
    # pipeline showed it does not.
    "formulaic", "interface_meta", "patsy",
    # Terminal line editing, in a process whose only input is a JSON-RPC pipe.
    # The interpreter links it, so PyInstaller collects it along with
    # libreadline and libtinfo. Proven unnecessary the same way.
    "readline",
]


#: Shared libraries whose only consumer was an excluded module. Excluding the
#: Python module does not always drop the .so PyInstaller collected alongside
#: it, and an orphaned library is exactly the sort of unexplained entry the
#: identity gate exists to refuse.
ORPHANED = ("libreadline", "libtinfo", "libncurses")


def strip_orphaned(items):
    """Drop libraries left behind by an excluded module."""
    kept = []
    for entry in items:
        name = Path(entry[0]).name.lower()
        if any(name.startswith(token) for token in ORPHANED):
            continue
        kept.append(entry)
    return kept


def strip_gpu(items):
    """Drop CUDA/cuBLAS artefacts.

    Not yet load-bearing -- nothing here links CUDA. It is in place before the
    model lands, because `ggml-cuda` and its cuBLAS runtime are roughly 950 MB
    and would blow the committed size budget in a single commit.
    """
    kept = []
    for entry in items:
        name = Path(entry[0]).name.lower()
        if any(token in name for token in ("cuda", "cublas", "cudnn", "cudart")):
            continue
        kept.append(entry)
    return kept


a = Analysis(
    [str(ROOT / "planbrain" / "backend" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
a.binaries = strip_orphaned(strip_gpu(a.binaries))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="planbrain-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip antivirus heuristics, and an
                        # unsigned installer has enough of that already
    console=True,       # the sidecar IS the stdio pipe; a windowed build has none
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="planbrain-backend",
)
