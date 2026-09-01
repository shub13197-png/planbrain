"""The bundle identity gate, and proof it can fail.

The size budget caught nothing: the first build was 267 MB against a 400 MB
budget and contained 83 MB of pyarrow, found only because someone read the
breakdown. Under budget is precisely when nobody looks.

Size was also the lesser problem. That 83 MB included `arrow_flight`, an RPC
library for moving data between machines, inside an application that promises
nothing leaves this one -- a claim-5 finding a size gate could never raise.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

from bundle_manifest import ALLOWED, FORBIDDEN, audit, main, normalise, weigh


def _tree(root: Path, layout):
    for name, size in layout.items():
        d = root / "_internal" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "payload.bin").write_bytes(b"x" * size)
    return root


def test_a_forbidden_package_fails_the_gate(tmp_path):
    """The regression that matters: a transitive bump quietly reinstating one."""
    _tree(tmp_path, {"pyarrow": 100, "numpy": 10})
    assert main([str(tmp_path)]) == 1


def test_an_unknown_package_fails_the_gate(tmp_path):
    """The general case. A new distribution needs a line saying why it ships."""
    _tree(tmp_path, {"somethingnew": 100})
    _, unexpected, _, _, _ = audit(tmp_path)
    assert "somethingnew" in unexpected


def test_a_clean_tree_passes(tmp_path):
    _tree(tmp_path, {"numpy": 10, "scipy": 10, "pandas": 10})
    _, unexpected, forbidden, _, _ = audit(tmp_path)
    assert unexpected == {}
    assert forbidden == {}


def test_an_empty_tree_is_refused_not_passed(tmp_path):
    """An empty bundle satisfies every check vacuously -- the empty-result
    failure this project keeps finding."""
    assert main([str(tmp_path)]) == 2


def test_versioned_names_fold_onto_their_package():
    """Otherwise every dependency bump reports a new entry and the gate becomes
    noise nobody reads."""
    assert normalise("numpy-2.4.3") == "numpy"
    assert normalise("libscipy_openblas64_-4bb64bb73.dll") == "scipy.libs"
    assert normalise("brandnew") == "brandnew"


def test_every_allowlist_entry_states_a_reason():
    """A name with no reason is a name nobody can review."""
    for name, reason in ALLOWED.items():
        assert len(reason) > 12, f"{name} has no real reason"


def test_every_forbidden_entry_states_why():
    for name, reason in FORBIDDEN.items():
        assert len(reason) > 12, f"{name} has no reason"


def test_the_tls_transport_layer_is_forbidden():
    """Found by this gate, not the size budget: 6 MB of OpenSSL in an app whose
    promise is that nothing leaves the machine. libcrypto stays -- _hashlib
    links it for hashing -- and libssl, the transport layer, does not."""
    assert "libssl-3" in FORBIDDEN
    assert "libcrypto-3" in ALLOWED
    assert "hashing" in ALLOWED["libcrypto-3"]


def test_the_network_clients_are_forbidden():
    for name in ("requests", "urllib3", "certifi", "fsspec", "pyarrow"):
        assert name in FORBIDDEN


def test_allowlist_and_forbidden_do_not_overlap():
    assert not (set(ALLOWED) & set(FORBIDDEN))


def test_weighing_a_missing_tree_is_empty_not_an_error(tmp_path):
    assert weigh(tmp_path / "nope") == {}


def test_pure_python_packages_are_checked_too(tmp_path):
    """The hole that made half the gate decorative.

    PyInstaller archives pure-Python packages inside the executable, where a
    directory walk cannot see them. `requests` is pure Python, so it sat in
    FORBIDDEN and could never have fired. The archive's TOC is read as well.
    """
    from bundle_manifest import archived_packages

    work = tmp_path / "work"
    work.mkdir()
    (work / "PYZ-00.toc").write_text(repr([
        ("os", "/usr/lib/python3.12/os.py", "PYMODULE"),
        ("requests", "/x/site-packages/requests/__init__.py", "PYMODULE"),
        ("requests.api", "/x/site-packages/requests/api.py", "PYMODULE"),
        ("numpy", "/x/site-packages/numpy/__init__.py", "PYMODULE"),
    ]), encoding="utf-8")

    found = archived_packages(work)
    assert "requests" in found, "a pure-Python client must be visible"
    assert "numpy" in found
    assert "os" not in found, "the stdlib is not something an allowlist enumerates"


def test_an_archived_forbidden_package_fails_the_gate(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "PYZ-00.toc").write_text(repr([
        ("requests", "/x/site-packages/requests/__init__.py", "PYMODULE"),
    ]), encoding="utf-8")
    _tree(tmp_path, {"numpy": 10})
    assert main([str(tmp_path), "--workpath", str(work)]) == 1


def test_a_missing_archive_toc_is_reported_not_ignored(tmp_path):
    """Half a gate that reports success is worse than no gate."""
    _tree(tmp_path, {"numpy": 10})
    assert main([str(tmp_path), "--workpath", str(tmp_path / "absent")]) == 1
