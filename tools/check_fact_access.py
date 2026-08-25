"""CI gate: only the accessor may read a fact table.

Fact storage is sparse, so a direct SELECT returns a plausible wrong answer
rather than an error. The database cannot enforce that; this can.

Reads are gated, writes are not -- absent-means-zero is a read hazard, and the
importer must still be able to INSERT.

    python -m tools.check_fact_access
"""

import re
import sys
from pathlib import Path

#: Files permitted to name a fact table in a read position. Keep this short;
#: adding to it should be a visible decision in a diff.
ALLOWED = {
    "planbrain/facts/access.py",  # the chokepoint itself
    "planbrain/facts/scenario.py",  # INSERT...SELECT copy, a write path
    "tools/check_fact_access.py",  # this file names the pattern it looks for
    # Storage-layer tests. These assert the constraints and the copy semantics
    # directly, which is the one job that cannot go through the accessor.
    "tests/test_fact_grain.py",
    "tests/test_scenario.py",
    "tests/test_fact_access_lint.py",
}

#: The invariant that matters: no module that computes a plan number is exempt.
#: Only the facts package itself may be allowlisted under planbrain/.
EXEMPT_PACKAGE_PREFIX = "planbrain/facts/"

READ_OF_FACT_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+(fact_\w+)", re.IGNORECASE)


def scan(root: Path) -> list[str]:
    """Return a violation message per offending line, empty if the tree is clean."""
    violations = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWED or ".venv" in rel:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            match = READ_OF_FACT_TABLE.search(line)
            if match:
                violations.append(
                    f"{rel}:{lineno}: direct read of {match.group(1)}; "
                    f"use planbrain.facts.access.read_facts"
                )
    return violations


def main() -> int:
    violations = scan(Path(__file__).resolve().parents[1])
    for v in violations:
        print(v, file=sys.stderr)
    if violations:
        print(
            f"\n{len(violations)} direct fact-table read(s). Fact storage is sparse: "
            "an inner join drops zero buckets and biases every statistic high.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
