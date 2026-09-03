"""What is in the bundle, why, and a gate that fails on anything new.

    python packaging/bundle_manifest.py build/dist/planbrain-backend
    python packaging/bundle_manifest.py build/dist/planbrain-backend --report

**Why this exists rather than the size budget alone.** The budget caught nothing:
the first build came in at 267 MB against a 400 MB budget, comfortably inside,
and the 83 MB of `pyarrow` it contained was found only because someone read the
breakdown. Under budget is precisely when nobody looks.

And size was the lesser problem. That 83 MB included `arrow_flight` — an RPC
library for moving data *between machines* — inside an application whose central
promise is that nothing leaves this one. **That is a claim-5 finding, not a size
one**, and a size gate would never have raised it however tight the number.

So the gate is on *identity*, not weight: every top-level entry in the bundle
needs an allowlist entry saying what it is and why it ships. A new distribution
appearing — pulled in by a transitive bump nobody reviewed — fails the build
until someone writes that line. Weight is reported alongside, because the
breakdown is what makes a bad entry obvious once you are already looking.

Same shape as the fact-access allowlist: the list may grow, and growing it is a
visible act in a diff rather than a default.

**The filesystem is only half the bundle.** PyInstaller lays native extensions
and data out on disk but archives pure-Python packages inside the executable,
where a directory walk cannot see them. The first version of this gate walked
only the tree -- so `requests`, which is pure Python, sat in `FORBIDDEN` and
could never have fired. The archive's table of contents is read too, and only
third-party modules (those from `site-packages`) need allowlisting; enumerating
the standard library would be hundreds of entries nobody would read.
"""

import argparse
import ast
import collections
import re
import sys
from pathlib import Path

#: Every top-level name expected in the bundle, and why it is there.
#: Adding an entry is a deliberate act. Deleting a stale one is required --
#: an allowlist entry for something no longer shipped is an exemption nobody
#: is reviewing.
ALLOWED = {
    # --- our own code and the interpreter -----------------------------
    "planbrain-backend": "the sidecar executable itself",
    "planbrain": "our package data: schema.sql and the payload contract",
    "python": "CPython runtime. Canonical: the file is python314.dll on Windows and libpython3.12.so.1.0 on Linux",
    "base_library.zip": "CPython stdlib archive",
    "unicodedata": "stdlib, str normalisation",
    "_decimal": "stdlib decimal",
    "pyexpat": "stdlib XML parser",
    "_elementtree": "stdlib XML, linked by the interpreter build",
    "_zstd": "stdlib compression (3.14)",
    "_bz2": "stdlib compression",
    "_lzma": "stdlib compression",
    "sqlite3": "the datastore -- single file, no service, no port",
    "_sqlite3": "sqlite3's native extension",
    "libffi": "ctypes, required by the interpreter",
    "_ctypes": "stdlib ctypes",
    "select": "stdlib selectors",
    "_socket": "stdlib socket. Present because the stdlib links it; every "
               "outbound connection through it is blocked by offline.engage()",
    "_hashlib": "hashing. Not transport -- see libcrypto below",
    "libcrypto": "OpenSSL's PRIMITIVES, linked by _hashlib for hashing. "
                 "libssl -- the TLS transport layer -- is excluded, which "
                 "is the part that mattered",
    "_queue": "stdlib queue primitives, used by multiprocessing",
    "_multiprocessing": "stdlib; statsforecast may parallelise fitting locally",
    "_asyncio": "stdlib event loop, linked by the interpreter build",
    "_overlapped": "Windows async primitives, stdlib",
    "_uuid": "stdlib uuid generation, linked by the interpreter",
    "_wmi": "Windows platform module, stdlib",
    "VCRUNTIME140": "MSVC runtime, required by every native extension on Windows",
    "VCRUNTIME140_1": "MSVC runtime, C++ half; required on Windows",
    "ucrtbase": "the Universal C Runtime -- the C standard library on Windows. "
                "Absent from a local 3.14 build and present on CI's 3.12, which "
                "is why an allowlist checked on one machine is not checked",
    "api-ms-win": "UCRT API-set forwarder stubs, collapsed to one entry. Thirteen "
                  "near-identical DLLs that resolve calls into ucrtbase",

    # --- the same job as VCRUNTIME, on the platforms that are not Windows.
    # Absent from this list until the first Linux and macOS builds ran, because
    # the allowlist had only ever seen a Windows bundle. Every one of these is a
    # compiler or C-library runtime that a native extension links; none of them
    # is a networking or serialisation library, which is what this gate is for.
    "libgcc_s": "GCC unwinder and soft-float helpers, linked by every "
                "compiled extension on Linux",
    "libstdc++": "C++ standard library; scipy's and pandas' extensions link it",
    "libgfortran": "Fortran runtime. scipy's LAPACK and BLAS kernels are "
                   "compiled Fortran, which is the whole reason it is here",
    "libquadmath": "128-bit float support required by libgfortran",
    "libz": "zlib. The stdlib zipfile module and openpyxl need it -- an .xlsx "
            "is a zip archive",
    "libbz2": "the compression library behind stdlib bz2",
    "liblzma": "the compression library behind stdlib lzma",
    "libuuid": "stdlib uuid on Linux",
    "libcom_err": "Kerberos error tables, pulled in by libuuid's dependency "
                  "chain on some distributions",

    # --- the numerical stack, which is the actual product -------------
    "numpy": "array maths under every engine",
    "numpy.libs": "numpy's bundled OpenBLAS",
    "scipy": "optimisation and statistics under statsforecast",
    "scipy.libs": "scipy's bundled OpenBLAS",
    "pandas": "statsforecast's frame interface",
    "pandas.libs": "pandas native extensions",
    "statsmodels": "state-space models behind AutoETS",
    "statsforecast": "the forecasting engines: AutoETS, Croston, TSB",
    "coreforecast": "statsforecast's native kernels",
    "utilsforecast": "statsforecast's helpers",
    "dateutil": "date parsing, pulled by pandas",
    "tqdm": "progress reporting inside statsforecast's fitting loop",

    # --- reading what customers actually send -------------------------
    "openpyxl": "reads .xlsx; a zip-and-XML reader with nothing network-facing",
    "et_xmlfile": "openpyxl's streaming XML writer",
    "yaml": "column-mapping profiles, loaded with safe_load only",
    "_yaml": "PyYAML's optional libyaml accelerator",

    # --- transitive, pure-Python, and genuinely used -------------------
    "six": "Python 2/3 shim, pulled by dateutil",
    "typing_extensions": "backported typing, pulled across the numeric stack",
    "joblib": "statsforecast's local parallelism over model fits",
    "narwhals": "utilsforecast's dataframe abstraction",
    "packaging": "version comparison, pulled widely",
    "colorama": "tqdm's Windows colour support",
    "cloudpickle": "pulled by statsforecast",
    "threadpoolctl": "caps BLAS threads; without it OpenBLAS grabs every core",
    "tzdata": "timezone database",
    "pytz": "timezone data, pulled by pandas",
    "attr": "attrs' legacy import name, pulled by jsonschema",
    "jsonschema": "validates every payload crossing the sidecar boundary",
    "referencing": "jsonschema's reference resolution -- local refs only",

    # --- contract validation ------------------------------------------
    "jsonschema_specifications": "draft metaschemas, shipped as package data",
    "rpds": "referencing's persistent data structures",
    "attrs": "pulled by jsonschema",
}

#: Names that must NEVER appear. Each was found in a real build and removed;
#: this stops a transitive bump quietly reinstating one.
FORBIDDEN = {
    "pyarrow": "83 MB, and arrow_flight is an RPC library for moving data "
               "between machines -- see docs/packaging.md",
    "fugue": "distributed execution we never use; drags in pyarrow",
    "triad": "fugue's dependency; drags in pyarrow",
    "adagio": "fugue's dependency",
    "fsspec": "filesystem abstraction with HTTP and S3 backends",
    "sklearn": "unused; 12.6 MB",
    "stockpyl": "test-only oracle, and it declares sphinx==4.5.0",
    "matplotlib": "no plotting in the sidecar",
    "bs4": "BeautifulSoup, an HTML scraping stack reached through "
           "pandas.read_html. Pure Python, so it hid from the gate until it "
           "learned to read the PYZ archive",
    "html5lib": "bs4's HTML parser",
    "soupsieve": "bs4's CSS selector engine",
    "webencodings": "html5lib's encoding detection",
    "lxml": "another HTML/XML parser reached through pandas.read_html",
    "IPython": "notebook display machinery; there is no notebook here",
    "pygments": "syntax highlighting for a REPL the sidecar does not have",
    "traitlets": "IPython's configuration system",
    "libssl": "the OpenSSL TLS TRANSPORT layer, in an app that promises "
                "nothing leaves the machine. Found by this gate, not by the "
                "size budget -- see docs/packaging.md",
    "requests": "an HTTP client has no place in the bundle",
    "urllib3": "requests' transport",
    "certifi": "a CA bundle is only useful for TLS we do not do",
    "pytest": "test framework has no business in a shipped bundle",
}


def archived_packages(workpath=None, dist=None) -> set:
    """Third-party top-level packages inside the executable's PYZ archive.

    Read from the build's ``PYZ-00.toc``, which PyInstaller writes as a Python
    literal next to the other build artefacts. Only entries sourced from
    ``site-packages`` are returned: the standard library is not something an
    allowlist should enumerate, and a network client that arrived as a
    dependency will always come from site-packages.

    Returns an empty set when the TOC is absent -- the caller reports that,
    because a silently empty result here would make the archive half of the
    gate pass vacuously.
    """
    if workpath:
        candidates = [Path(workpath)]
    elif dist is not None:
        # Relative to the bundle being audited, never the working directory.
        # Searching `build/work` from cwd let a stale TOC from an unrelated
        # build satisfy the archive half of the gate -- which is the same
        # class of failure this gate exists to catch.
        # Only the sibling work directory PyInstaller writes, never a parent.
        # Recursing from a parent found an unrelated build's TOC -- the same
        # too-broad-search failure this gate keeps catching elsewhere.
        root = Path(dist).resolve()
        candidates = [root.parent.parent / "work"]
    else:
        candidates = []

    for base in candidates:
        for toc in sorted(base.rglob("PYZ-*.toc")) if base.is_dir() else []:
            try:
                parsed = ast.literal_eval(toc.read_text(encoding="utf-8"))
            except (ValueError, SyntaxError):
                continue
            entries = parsed[1] if isinstance(parsed, tuple) else parsed
            found = set()
            for entry in entries:
                name, source = entry[0], str(entry[1])
                if "site-packages" in source.replace("\\", "/"):
                    found.add(name.split(".")[0])
            if found:
                return found
    return set()


def weigh(root: Path) -> dict:
    """Bytes per top-level entry in a PyInstaller one-dir tree."""
    totals = collections.Counter()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        name = parts[1] if len(parts) > 1 and parts[0] == "_internal" else parts[0]
        for suffix in (".pyd", ".dll", ".so", ".exe", ".py", ".dist-info"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        totals[name] += path.stat().st_size
    return dict(totals)


#: Where a shared library's name stops and the platform's noise begins.
_EXTENSION = re.compile(r"\.(?:so|dylib|dll|pyd)\b")
#: auditwheel and PyInstaller stamp content hashes into vendored library names:
#: libgfortran-83c28eba-468e71e5.so.5.0.0
_HASH = re.compile(r"-[0-9a-f]{8,}")
#: A trailing soversion, whether written libcrypto-3.dll or libcrypto.so.3.
_SOVERSION = re.compile(r"[-.]\d+(?:\.\d+)*$")
#: python314.dll, libpython3.12.so.1.0 -- the same interpreter.
_INTERPRETER = re.compile(r"(?:lib)?python[\d._]*$", re.IGNORECASE)


def canonical(name: str) -> str:
    """One name for the same library on every platform.

    The allowlist was written from a single Windows build and every entry in it
    carried Windows spelling -- `libcrypto-3`, `libffi-8`, `python314`. The first
    Linux and macOS builds therefore reported *every* shared library as new, and
    two genuinely new packages were buried in twenty lines of noise. A gate that
    cries wolf on a platform change is a gate that gets skimmed.

    Rejected: a per-platform section in the allowlist. That would let a package
    be permitted on Linux and forbidden on Windows with nobody seeing the
    asymmetry, which is the failure this gate exists to prevent, moved one level
    up.

    Version digits are stripped only after a separator, so `libbz2` and
    `VCRUNTIME140` keep the digits that are part of their names rather than
    becoming `libbz` and `VCRUNTIME`.
    """
    stem = _EXTENSION.split(name, maxsplit=1)[0]
    if stem.lower().startswith("api-ms-win"):
        # Windows API-set forwarders: thirteen near-identical stubs
        # (api-ms-win-crt-math-l1-1-0 and friends) that are all one thing, the
        # UCRT redistributable. Thirteen allowlist lines saying the same
        # sentence is thirteen lines nobody reads.
        return "api-ms-win"
    stem = _HASH.sub("", stem)
    stem = _SOVERSION.sub("", stem)
    if _INTERPRETER.fullmatch(stem):
        return "python"
    return stem


def normalise(name: str) -> str:
    """Strip the version and architecture noise off a shipped filename.

    `libscipy_openblas64_-4bb64bb.dll` and `numpy-2.4.3.dist-info` are the same
    thing as `scipy.libs` and `numpy` for allowlist purposes, and treating them
    as new entries every release would make the gate noise nobody reads.
    """
    if name in ALLOWED:
        return name
    if name.startswith("lib") and "openblas" in name.lower():
        return "scipy.libs"
    reduced = canonical(name)
    if reduced in ALLOWED:
        return reduced
    # Unix prefixes shared libraries with `lib` and Windows does not:
    # sqlite3.dll and libsqlite3.so.0 are one library. Tried only after the
    # canonical form has failed, so `libcrypto` and `libz` -- which are
    # allowlisted under their own names -- resolve before the prefix is touched.
    if reduced.startswith("lib") and reduced[3:] in ALLOWED:
        return reduced[3:]
    for token in ("-", "."):
        head = name.split(token)[0]
        if head in ALLOWED:
            return head
    return reduced


def audit(root: Path, workpath=None):
    """Return (weights, unexpected, forbidden_found, archived, toc_seen).

    ``weights`` merges the filesystem tree with the archive, so an archived
    package appears at zero bytes -- its identity is the point, not its size.
    Callers checking for an empty bundle must use ``weigh()`` directly, or a
    stray TOC would make an empty tree look populated.
    """
    weights = weigh(root)
    resolved = {}
    for name, size in weights.items():
        # Resolve against BOTH lists. Checking only ALLOWED meant a forbidden
        # library under a non-Windows filename -- libssl.so.3 rather than
        # libssl-3.dll -- kept its raw name and was reported as merely unknown.
        # The build still failed, so this was never a silent pass, but the one
        # finding the gate exists for would have arrived as one more line in a
        # list of twenty.
        reduced = normalise(name)
        key = reduced if (reduced in ALLOWED or reduced in FORBIDDEN) else name
        resolved[key] = resolved.get(key, 0) + size

    # Pure-Python packages live in the archive, not on disk. They carry no
    # filesystem weight, so they enter at zero bytes -- their identity is the
    # point, not their size.
    archived = archived_packages(workpath, dist=root)
    for name in archived:
        resolved.setdefault(name, 0)

    unexpected = {n: s for n, s in resolved.items() if n not in ALLOWED}
    forbidden_found = {n: s for n, s in resolved.items() if n in FORBIDDEN}
    return resolved, unexpected, forbidden_found, archived, bool(archived)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist")
    parser.add_argument("--report", action="store_true",
                        help="print the full weight breakdown and exit 0")
    parser.add_argument("--workpath", default=None,
                        help="PyInstaller work directory holding PYZ-*.toc")
    args = parser.parse_args(argv)

    root = Path(args.dist)
    if not root.is_dir():
        print(f"{root} is not a directory -- did the build run?", file=sys.stderr)
        return 2

    if not weigh(root):
        print(f"{root} contains no files -- did the build run?", file=sys.stderr)
        return 2

    weights, unexpected, forbidden_found, archived, toc_seen = audit(
        root, args.workpath
    )

    total = sum(weights.values())
    print(f"bundle: {total / 1e6:,.1f} MB across {len(weights)} top-level entries")
    if toc_seen:
        print(f"        {len(archived)} third-party package(s) inside the archive")
    else:
        print("        (no PYZ table of contents found -- archive NOT checked)")
    print()
    for name, size in sorted(weights.items(), key=lambda kv: -kv[1])[:15]:
        why = ALLOWED.get(name, "** NOT ALLOWLISTED **")
        print(f"  {size / 1e6:8.2f} MB  {name:26s} {why[:52]}")

    if args.report:
        return 0

    problems = []
    for name, size in sorted(forbidden_found.items(), key=lambda kv: -kv[1]):
        problems.append(
            f"FORBIDDEN: {name} ({size / 1e6:.1f} MB) is back in the bundle. "
            f"{FORBIDDEN[name]}"
        )
    for name, size in sorted(unexpected.items(), key=lambda kv: -kv[1]):
        if name in FORBIDDEN:
            continue
        problems.append(
            f"NEW: {name} ({size / 1e6:.1f} MB) is not in the allowlist. If it "
            f"belongs, add it to ALLOWED with a reason; if it does not, exclude "
            f"it in packaging/backend.spec."
        )
    if not toc_seen:
        problems.append(
            "the PYZ table of contents was not found, so pure-Python packages "
            "were not checked. Run this straight after a build, or pass "
            "--workpath. An unchecked half is not a passing gate."
        )

    if problems:
        print()
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print()
    print("every entry is allowlisted with a reason; nothing forbidden present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
