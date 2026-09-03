"""Hold each installer to the budget committed before the first build.

    python packaging/check_installer_size.py dist/

**This gate did not exist until the first installers were produced**, and that
is the finding it exists to record. `check_size.py` measured the *backend*
directory, which is one input to an installer; the committed installer budget in
`docs/packaging.md` -- 150 MB compressed -- had nothing measuring it. The first
Windows MSI came out at 315 MB, more than twice the budget, and nothing failed.

Same shape as the size budget that passed while 83 MB of pyarrow sat inside the
bundle, and the same shape as the forbidden list that could not fire: a limit
nobody measures is a sentence in a document.

Budgets are per file, not per platform total, because a user downloads one file.
"""

import argparse
import sys
from pathlib import Path

#: Committed in docs/packaging.md before the first build. Exceeding this is a
#: finding to report and diagnose, not a number to adjust -- so raising it takes
#: an edit here, an edit to the document, and a reason someone has to write.
BUDGET_MB = 150.0

#: Extensions this understands. Anything else in the directory is reported and
#: not measured, rather than silently skipped -- a new bundle format arriving
#: unmeasured is exactly how the first one got to 315 MB unnoticed.
INSTALLERS = (".msi", ".exe", ".dmg", ".appimage", ".deb")


def weigh(root: Path) -> list:
    return sorted(
        (p for p in root.rglob("*") if p.is_file()
         and p.suffix.lower() in INSTALLERS),
        key=lambda p: -p.stat().st_size,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--budget-mb", type=float, default=BUDGET_MB)
    args = parser.parse_args(argv)

    root = Path(args.directory)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    installers = weigh(root)
    if not installers:
        # An empty result must not read as a pass. A build that produced no
        # installer would otherwise report "every installer is within budget",
        # which is true and worthless.
        print(f"no installer found under {root}; nothing was measured",
              file=sys.stderr)
        return 1

    over = []
    print(f"installer budget: {args.budget_mb:.0f} MB per file")
    for path in installers:
        size_mb = path.stat().st_size / 1e6
        flag = "OVER" if size_mb > args.budget_mb else "ok"
        print(f"  {size_mb:8.1f} MB  {flag:>4s}  {path.name}")
        if size_mb > args.budget_mb:
            over.append((path.name, size_mb))

    if over:
        print(file=sys.stderr)
        for name, size_mb in over:
            print(f"  {name} is {size_mb:.0f} MB against a {args.budget_mb:.0f} MB "
                  f"budget, {size_mb / args.budget_mb:.1f}x over", file=sys.stderr)
        print(
            "\n  This is a finding, not a number to adjust. Diagnose what is in "
            "the installer before touching the budget -- see docs/packaging.md.",
            file=sys.stderr,
        )
        return 1

    print(f"\nall {len(installers)} installer(s) within budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
