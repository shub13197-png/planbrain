# PyInstaller spec for the Planning Brain command, for people without Python.
#
#     pyinstaller packaging/cli.spec --noconfirm
#
# **Why this exists beside backend.spec.** That one builds the sidecar: a
# JSON-RPC server speaking stdio to the desktop shell, correct for the shell and
# not something a person runs. This one builds `planbrain.exe`, the command in
# `planbrain/cli.py`.
#
# It is the install path that works today. The desktop bundle needs a Rust
# toolchain to compile the Tauri shell, and `docs/status.md` records that the
# two-installer path has never completed a green run. This needs neither Rust
# nor Python on the target machine.
#
# One-dir, not one-file, for the reason backend.spec gives: one-file unpacks the
# whole application to a temp directory on every launch, which is a poor look in
# a product whose promise is that nothing leaves the machine.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

# The same lazy imports backend.spec names. statsforecast reaches into scipy and
# statsmodels only when a model is fitted, which static analysis cannot see and
# which therefore fails on a customer's machine rather than in this build.
hiddenimports = [
    "planbrain.backend.api",
    "planbrain.cli",
    "planbrain.facts.master",
    "planbrain.mapping",
    "openpyxl",
    "yaml",
    *collect_submodules("statsforecast"),
    *collect_submodules("utilsforecast"),
    "scipy.stats",
    "scipy.special",
    "scipy._lib.array_api_compat",
]

# schema.sql is read at runtime by api.session(). Shipping the code without it
# produces a binary that starts and then cannot create a database.
datas = [
    (str(ROOT / "planbrain" / "facts" / "schema.sql"), "planbrain/facts"),
    *collect_data_files("statsforecast"),
]

a = Analysis(
    # Not planbrain/cli.py directly: see packaging/cli_entry.py.
    [str(ROOT / "packaging" / "cli_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="planbrain",
    console=True,
    debug=False,
    strip=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="planbrain-cli",
)
