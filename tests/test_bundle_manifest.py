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

from bundle_manifest import (
    ALLOWED, FORBIDDEN, audit, canonical, main, normalise, weigh,
)


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
    assert "libssl" in FORBIDDEN
    assert "libcrypto" in ALLOWED
    assert "hashing" in ALLOWED["libcrypto"]


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


# --------------------------------------------------------------------------
# one name per library, on every platform
# --------------------------------------------------------------------------

#: Exactly what the Windows, Linux and macOS runners reported, the first time
#: this gate ever saw a bundle it had not been written against. Kept verbatim
#: rather than paraphrased: these strings are the evidence.
OBSERVED = {
    "libcrypto-3.dll": "libcrypto",
    "libcrypto.so.3": "libcrypto",
    "libffi-8.dll": "libffi",
    "libffi.so.8": "libffi",
    "python314.dll": "python",
    "libpython3.12.so.1.0": "python",
    "libgfortran-83c28eba-468e71e5.so.5.0.0": "libgfortran",
    "libquadmath-2284e583-a9307bba.so.0.0.0": "libquadmath",
    "libstdc++.so.6": "libstdc++",
    "libgcc_s.so.1": "libgcc_s",
    "libz.so.1": "libz",
    "libbz2.so.1.0": "libbz2",
    "liblzma.so.5": "liblzma",
    "libuuid.so.1": "libuuid",
    "_bz2.cpython-312-x86_64-linux-gnu.so": "_bz2",
    "numpy-2.4.3.dist-info": "numpy",
    "numpy.libs": "numpy.libs",
    "VCRUNTIME140.dll": "VCRUNTIME140",
    "VCRUNTIME140_1.dll": "VCRUNTIME140_1",
}


def test_every_name_the_runners_reported_resolves_to_one_allowlisted_entry():
    """The allowlist was written from a single Windows build.

    The first Linux and macOS builds therefore reported *every* shared library
    as new -- twenty lines of noise with two genuinely new packages buried in
    it. A gate that cries wolf on a platform change is a gate that gets skimmed,
    which is worse than no gate, because it comes with a green tick most of the
    time.
    """
    wrong = {
        name: normalise(name)
        for name, expected in OBSERVED.items()
        if normalise(name) != expected
    }
    assert wrong == {}, f"normalised to the wrong entry: {wrong}"

    unlisted = sorted(n for n in OBSERVED.values() if n not in ALLOWED)
    assert unlisted == [], f"resolves cleanly but is not allowlisted: {unlisted}"


def test_the_allowlist_is_written_in_platform_neutral_names():
    """The rule, not the fix.

    Every key must already be canonical, so a Windows-spelled entry like
    `libcrypto-3` or `python314` cannot come back -- which is how the list got
    into a state where two thirds of the platforms it governed had never been
    checked against it.
    """
    windows_shaped = sorted(k for k in ALLOWED if canonical(k) != k)
    assert windows_shaped == [], (
        f"these carry a platform's spelling and would not match elsewhere: "
        f"{windows_shaped}"
    )


def test_version_digits_that_are_part_of_a_name_survive():
    """`libbz2` is not `libbz`, and `VCRUNTIME140` is not `VCRUNTIME`.

    Stripping digits unconditionally is the obvious implementation and it
    silently merges distinct libraries into one allowlist entry, which is an
    exemption nobody wrote.
    """
    for name in ("libbz2", "VCRUNTIME140", "VCRUNTIME140_1", "_bz2", "libgcc_s"):
        assert canonical(name) == name


def test_the_allowlist_has_no_duplicate_entries():
    """A repeated key in a dict literal is invisible: the later one wins and the
    earlier reason is silently discarded. Two entries were duplicated this way.
    """
    import ast

    source = (Path(__file__).resolve().parents[1] / "packaging" / "bundle_manifest.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ALLOWED":
            keys = [k.value for k in node.value.keys]
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            assert duplicates == [], f"repeated allowlist keys: {duplicates}"
            return
    raise AssertionError("ALLOWED is no longer a dict literal; this test cannot check it")


def test_libraries_orphaned_by_an_exclusion_are_dropped_too():
    """Excluding a Python module does not always drop the shared library
    PyInstaller collected beside it. `readline` is excluded, and libreadline and
    libtinfo would otherwise stay -- terminal line-editing libraries in a
    process whose only input is a pipe, and exactly the kind of unexplained
    entry this gate refuses."""
    spec = (Path(__file__).resolve().parents[1] / "packaging" / "backend.spec")
    text = spec.read_text(encoding="utf-8")
    assert "def strip_orphaned" in text
    assert "a.binaries = strip_orphaned(strip_gpu(a.binaries))" in text
    for token in ("libreadline", "libtinfo"):
        assert token in text
        assert token not in ALLOWED, (
            f"{token} is both excluded from the bundle and allowlisted in it"
        )


def test_the_forbidden_list_is_written_in_platform_neutral_names_too():
    """`FORBIDDEN` had the same Windows spelling as the allowlist, and it
    matters more here: an allowlist miss is noise, a forbidden miss is the
    finding the gate was built to produce arriving as one line among twenty."""
    windows_shaped = sorted(k for k in FORBIDDEN if canonical(k) != k)
    assert windows_shaped == [], windows_shaped


@pytest.mark.parametrize("filename", ["libssl-3.dll", "libssl.so.3", "libssl.3.dylib"])
def test_the_tls_layer_is_caught_under_every_platform_spelling(tmp_path, filename):
    """The falsification. Before this, `audit` resolved a name against ALLOWED
    only and kept the raw filename otherwise, so the Linux and macOS spellings
    never reached the FORBIDDEN check at all."""
    _tree(tmp_path, {filename: 6_000_000})
    _weights, _unexpected, forbidden_found, _archived, _toc = audit(tmp_path)
    assert "libssl" in forbidden_found, (
        f"{filename} was not recognised as the forbidden TLS layer"
    )

