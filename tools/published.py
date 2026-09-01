"""The register of every figure this project publishes, and where it appears.

**Tests protect code. Pins protect claims. They are not the same job.**

The test suite asserts behaviour, deliberately and correctly — that is why 449
passing tests did not notice when every published number went stale. Removing
one `rng.choice` shifted a deterministic stream, the demo became a different
dataset, and the README carried on quoting figures from the old one. Nothing
was broken. Everything was wrong.

Behaviour tests cannot catch that, because the behaviour did not change. A
published figure needs a **pin**: the value, and the exact string as it appears
in the prose, checked against a fresh computation on both sides.

Two links, and both are asserted in `tests/test_published_numbers.py`:

1. **register ↔ code** — the value here matches what the pipeline computes now.
2. **register ↔ docs** — the literal string here appears in every file listed.

Break either and CI fails. Changing a figure then costs a deliberate edit here,
which is the point: a claim should not be able to change by accident.
"""

from dataclasses import dataclass, field

README = "README.md"
SERVICE = "docs/service-backtest.md"
RCCP = "docs/rccp.md"
HAULPLAN = "docs/haulplan.md"
DEMOD = "docs/demo.md"


@dataclass(frozen=True)
class Figure:
    """One published number.

    ``literal`` is how it is written in the prose, so a doc that quietly drifts
    from the register fails rather than merely disagreeing.
    """

    key: str
    value: float
    literal: str
    where: tuple
    tolerance: float = 0.0
    note: str = ""


#: Every figure quoted in prose. Grouped by what produces it.
FIGURES = (
    # --- the dataset ----------------------------------------------------
    Figure("parts", 200, "200 SKUs", (README,), note="demo shape"),
    Figure("demand_series", 222, "All 222 series", (README,)),
    Figure("history_days", 546, "546 daily buckets", (DEMOD,)),

    # --- service, full portfolio, seed 7 --------------------------------
    Figure("fitted_fill", 0.972, "**97.2%**", (README,), tolerance=0.002),
    Figure("tuned_fill", 0.957, "95.7%", (README,), tolerance=0.002),
    Figure("stale_fill", 0.934, "93.4%", (README,), tolerance=0.002),
    Figure("naive_fill", 0.761, "76.1%", (README,), tolerance=0.002),
    Figure("intermittent_fitted", 0.973, "97.3%", (README,), tolerance=0.002),
    Figure("intermittent_tuned", 0.956, "95.6%", (README,), tolerance=0.002),
    Figure("lumpy_fitted", 0.928, "92.8%", (README,), tolerance=0.002,
           note="the retraction: a tuned reorder point beats this"),
    Figure("lumpy_tuned", 0.937, "93.7%", (README,), tolerance=0.002),
    Figure("naive_intermittent", 0.630, "63.0%", (README,), tolerance=0.002),
    Figure("naive_lumpy", 0.551, "55.1%", (README,), tolerance=0.002),

    # --- the proof-of-value report, which was NOT covered until the guard
    # --- sweep found it disagreeing with the README for weeks -------------
    Figure("sb_fitted", 0.972, "**97.2%**", (SERVICE,), tolerance=0.002),
    Figure("sb_tuned", 0.957, "95.7%", (SERVICE,), tolerance=0.002),
    Figure("sb_stale", 0.934, "93.4%", (SERVICE,), tolerance=0.002),
    Figure("sb_naive", 0.761, "76.1%", (SERVICE,), tolerance=0.002),
    Figure("sb_lumpy_fitted", 0.928, "92.8%", (SERVICE,), tolerance=0.002),
    Figure("sb_lumpy_tuned", 0.937, "93.7%", (SERVICE,), tolerance=0.002),
    Figure("sb_capped_fitted", 0.970, "**97.0% / 1,272**", (SERVICE,),
           tolerance=0.002),

    # --- capacity -------------------------------------------------------
    Figure("rccp_utilisation", 0.85, "85% overall utilisation", (README,),
           tolerance=0.01),
    Figure("rccp_overloaded", 139, "139 of 450", (README,)),

    # --- fairness -------------------------------------------------------
    Figure("fairness_opening", 0.8288, "0.8288", (HAULPLAN,), tolerance=0.001),
    Figure("fairness_after", 0.8865, "0.8865", (HAULPLAN,), tolerance=0.001),
    Figure("fairness_ceiling", 0.8898, "0.8898", (HAULPLAN,), tolerance=0.001),
)


@dataclass
class Computed:
    """What the pipeline produces now, keyed the same way as FIGURES."""

    values: dict = field(default_factory=dict)

    def mismatches(self):
        """Figures whose published value no longer matches a fresh run."""
        out = []
        for figure in FIGURES:
            if figure.key not in self.values:
                continue
            actual = self.values[figure.key]
            if abs(actual - figure.value) > figure.tolerance:
                out.append((figure, actual))
        return out


def by_key(key: str) -> Figure:
    for figure in FIGURES:
        if figure.key == key:
            return figure
    raise KeyError(f"no published figure named {key!r}")
