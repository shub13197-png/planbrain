"""Fail the build if the sidecar exceeds the budget committed before it existed.

    python packaging/check_size.py build/dist/planbrain-backend

Bundle bloat is a silent failure: nobody notices 400 MB of unused libraries
until a download does not finish on a rural connection, which is the exact
deployment this product targets. So it fails CI rather than being noticed.
"""

import sys
from pathlib import Path

#: docs/packaging.md, committed in cc1f423 before the first build. Excludes any
#: bundled model, which is measured separately or it would hide everything else.
BUDGET_MB = 400


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: check_size.py <dist-dir>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    if not root.is_dir():
        print(f"{root} is not a directory -- did the build run?", file=sys.stderr)
        return 2

    files = [f for f in root.rglob("*") if f.is_file()]
    if not files:
        # An empty tree would otherwise pass the budget comfortably.
        print(f"{root} contains no files", file=sys.stderr)
        return 2

    total = sum(f.stat().st_size for f in files)
    mb = total / 1e6
    print(f"sidecar: {mb:,.0f} MB across {len(files):,} files (budget {BUDGET_MB} MB)")

    biggest = sorted(files, key=lambda f: -f.stat().st_size)[:5]
    for f in biggest:
        print(f"  {f.stat().st_size / 1e6:7.1f} MB  {f.relative_to(root)}")

    if mb > BUDGET_MB:
        print(
            f"\nOVER BUDGET by {mb - BUDGET_MB:,.0f} MB. This is a finding to "
            f"diagnose, not a number to adjust -- check what a hook pulled in "
            f"before raising the budget in docs/packaging.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
