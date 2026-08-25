"""CI gate: only the facts package may touch a fact table directly.

Two hazards, both invisible at the point of failure:

* **Reads.** Fact storage is sparse, so a direct SELECT that inner-joins drops
  the zero buckets and returns a plausible wrong answer rather than an error.
* **Writes.** Frozen scenarios are enforced in ``write_facts``, not by a
  database trigger, so raw SQL routes around the only thing making a committed
  snapshot immutable. Raw writes also skip sparsification, materialising zero
  rows that ``read_facts`` would then hand back indistinguishably.

The table list is derived from ``planbrain.facts.grains``, so a grain added
later is covered without touching this file.

    python -m tools.check_fact_access
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain.facts.grains import FACT_TABLES  # noqa: E402

#: Files permitted to name a fact table in SQL. Keep this short; adding to it
#: should be a visible decision in a diff.
ALLOWED = {
    "planbrain/facts/access.py",  # the chokepoint itself
    "planbrain/facts/scenario.py",  # bulk INSERT...SELECT copy, shares the guard
    "tools/check_fact_access.py",  # this file names the pattern it looks for
    # Storage-layer tests. These assert the constraints, the copy semantics and
    # the gate itself, which is the one job that cannot go through the accessor.
    "tests/test_fact_grain.py",
    "tests/test_scenario.py",
    "tests/test_access.py",
    "tests/test_fact_access_lint.py",
}

#: The invariant that matters: no module that computes a plan number is exempt.
#: Only the facts package itself may be allowlisted under planbrain/.
EXEMPT_PACKAGE_PREFIX = "planbrain/facts/"

_TABLES = "|".join(sorted(FACT_TABLES))

#: Reads and writes are both gated. Ordered so that DELETE FROM is reported as a
#: write rather than as a read.
PATTERNS = [
    ("write", re.compile(rf"\bINSERT\s+INTO\s+({_TABLES})\b", re.IGNORECASE)),
    ("write", re.compile(rf"\bUPDATE\s+({_TABLES})\b", re.IGNORECASE)),
    ("write", re.compile(rf"\bDELETE\s+FROM\s+({_TABLES})\b", re.IGNORECASE)),
    ("read", re.compile(rf"\b(?:FROM|JOIN)\s+({_TABLES})\b", re.IGNORECASE)),
]

ADVICE = {
    "read": "use planbrain.facts.access.read_facts",
    "write": "use planbrain.facts.access.write_facts",
}


def scan(root: Path) -> list[str]:
    """Return a violation message per offending line, empty if the tree is clean."""
    violations = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWED or ".venv" in rel:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for kind, pattern in PATTERNS:
                match = pattern.search(line)
                if match:
                    violations.append(
                        f"{rel}:{lineno}: direct {kind} of {match.group(1)}; {ADVICE[kind]}"
                    )
                    break  # one finding per line; the first match names the hazard
    return violations


def main() -> int:
    violations = scan(Path(__file__).resolve().parents[1])
    for v in violations:
        print(v, file=sys.stderr)
    if violations:
        print(
            f"\n{len(violations)} direct fact-table access(es). Reads must densify "
            "against the bucket spine; writes must sparsify and respect frozen "
            "scenarios. Neither is enforced by the database.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
