"""`datasets/` is outside the application boundary, and that is enforced.

`docs/offline.md` draws the line: a developer choosing to download a public
dataset is not the application reaching the network. A line drawn only in prose
is a line that moves, so these assert it.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
APP_TREES = ("planbrain", "tools")


def _python_files(tree: Path):
    return [f for f in tree.rglob("*.py") if "__pycache__" not in str(f)]


def test_the_directory_exists_and_has_scripts():
    """A vacuous pass if this ever became empty."""
    assert DATASETS.is_dir()
    assert _python_files(DATASETS)


def test_no_application_code_imports_a_dataset_script():
    """The boundary. If `planbrain` or `tools` imported this, the fetch would be
    inside the app and the offline guarantee would be a lie."""
    offenders = []
    for tree in APP_TREES:
        for path in _python_files(ROOT / tree):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name.split(".")[0] in ("datasets", "fetch_m5"):
                        offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert offenders == [], offenders


def test_no_application_code_shells_out_to_a_dataset_script():
    """Invoking it as a subprocess would evade the import check above."""
    offenders = []
    for tree in APP_TREES:
        for path in _python_files(ROOT / tree):
            text = path.read_text(encoding="utf-8")
            if "fetch_m5" in text or "datasets/" in text.replace("docs/", ""):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


def test_the_dataset_scripts_are_not_in_the_shipped_bundle():
    """The spec analyses planbrain/backend/__main__.py, so nothing under
    datasets/ can be reached -- asserted because "cannot be reached" is the
    kind of claim that quietly stops being true."""
    spec = (ROOT / "packaging" / "backend.spec").read_text(encoding="utf-8")
    assert "datasets" not in spec


def test_the_fetch_script_says_it_is_outside_the_boundary():
    """Someone reading only the file must learn the same thing as someone
    reading docs/offline.md."""
    text = (DATASETS / "fetch_m5.py").read_text(encoding="utf-8")
    assert "Not part of the application" in text
    assert "offline.md" in text


def test_the_fetch_script_refuses_without_credentials(tmp_path, monkeypatch):
    """It must not fail obscurely deep in a CLI. This is the path every first
    run takes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fetch_m5", DATASETS / "fetch_m5.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    monkeypatch.setattr(module.Path, "home", staticmethod(lambda: tmp_path))

    assert module.main(["--out", str(tmp_path / "m5")]) == 2


def test_verify_reports_a_missing_download_rather_than_passing(tmp_path):
    """An absent dataset must not read as a successful verify."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fetch_m5", DATASETS / "fetch_m5.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.verify(tmp_path) == 1


def test_the_expected_files_are_named_not_globbed():
    """A changed competition layout must fail loudly rather than produce a
    partial dataset that looks fine until a forecast is fitted to two thirds of
    the history."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fetch_m5", DATASETS / "fetch_m5.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "sales_train_evaluation.csv" in module.EXPECTED
    assert "calendar.csv" in module.EXPECTED
    assert all(len(v) > 8 for v in module.EXPECTED.values())
