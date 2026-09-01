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
"""

import argparse
import collections
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
    "python314": "CPython runtime",
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
    "libffi-8": "ctypes, required by the interpreter",
    "_ctypes": "stdlib ctypes",
    "select": "stdlib selectors",
    "_socket": "stdlib socket. Present because the stdlib links it; every "
               "outbound connection through it is blocked by offline.engage()",
    "_hashlib": "hashing. Not transport -- see libcrypto-3 below",
    "libcrypto-3": "OpenSSL's PRIMITIVES, linked by _hashlib for hashing. "
                   "libssl-3 -- the TLS transport layer -- is excluded, which "
                   "is the part that mattered",
    "_queue": "stdlib queue primitives, used by multiprocessing",
    "_multiprocessing": "stdlib; statsforecast may parallelise fitting locally",
    "_asyncio": "stdlib event loop, linked by the interpreter build",
    "_overlapped": "Windows async primitives, stdlib",
    "_uuid": "stdlib uuid generation, linked by the interpreter",
    "_wmi": "Windows platform module, stdlib",
    "VCRUNTIME140": "MSVC runtime, required by every native extension on Windows",
    "VCRUNTIME140_1": "MSVC runtime, C++ half; required on Windows",

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
    "pytz": "timezone data, pulled by pandas",
    "dateutil": "date parsing, pulled by pandas",
    "tqdm": "progress reporting inside statsforecast's fitting loop",

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
    "libssl-3": "the OpenSSL TLS TRANSPORT layer, in an app that promises "
                "nothing leaves the machine. Found by this gate, not by the "
                "size budget -- see docs/packaging.md",
    "requests": "an HTTP client has no place in the bundle",
    "urllib3": "requests' transport",
    "certifi": "a CA bundle is only useful for TLS we do not do",
    "pytest": "test framework has no business in a shipped bundle",
}


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


def normalise(name: str) -> str:
    """Strip the version and architecture noise off a shipped filename.

    `libscipy_openblas64_-4bb64bb.dll` and `numpy-2.4.3.dist-info` are the same
    thing as `scipy.libs` and `numpy` for allowlist purposes, and treating them
    as new entries every release would make the gate noise nobody reads.
    """
    if name in ALLOWED:
        return name
    for token in ("-", "."):
        head = name.split(token)[0]
        if head in ALLOWED:
            return head
    if name.startswith("lib") and "openblas" in name.lower():
        return "scipy.libs"
    return name


def audit(root: Path):
    """Return (weights, unexpected, forbidden_found, stale_allowlist)."""
    weights = weigh(root)
    resolved = {}
    for name, size in weights.items():
        resolved[normalise(name) if normalise(name) in ALLOWED else name] = (
            resolved.get(normalise(name) if normalise(name) in ALLOWED else name, 0) + size
        )

    unexpected = {n: s for n, s in resolved.items() if n not in ALLOWED}
    forbidden_found = {n: s for n, s in resolved.items() if n in FORBIDDEN}
    stale = sorted(set(ALLOWED) - set(resolved))
    return resolved, unexpected, forbidden_found, stale


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist")
    parser.add_argument("--report", action="store_true",
                        help="print the full weight breakdown and exit 0")
    args = parser.parse_args(argv)

    root = Path(args.dist)
    if not root.is_dir():
        print(f"{root} is not a directory -- did the build run?", file=sys.stderr)
        return 2

    weights, unexpected, forbidden_found, stale = audit(root)
    if not weights:
        print(f"{root} contains no files", file=sys.stderr)
        return 2

    total = sum(weights.values())
    print(f"bundle: {total / 1e6:,.1f} MB across {len(weights)} top-level entries")
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
    if stale:
        problems.append(
            f"STALE: {', '.join(stale)} allowlisted but not shipped -- an "
            f"exemption nobody is reviewing. Remove the entries."
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
