"""Service against inventory, on real demand, against the tools an SME has.

    python -m tools.benchmark --data data/online-retail

**This reads a directory it is given and knows nothing about how the data got
there.** The fetch script lives outside the application boundary and this file
is inside it, so naming its path here would put a reference to the fetcher in
application code -- which `tests/test_dataset_boundary.py` forbids, and did
catch. The two-step instruction lives in `docs/benchmark.md` and in the fetch
script's own `--help`.

**Why this exists.** Every service figure this project has published came from a
generated dataset, and `docs/method.md` names that synthetic-world ceiling at
the top of the page: a generator can only produce demand its author thought to
model, so a policy tuned on it is tuned on an assumption. This runs the same
comparison on a real transaction log from a real small business.

**The comparison is a curve, not a number.** Any inventory policy can reach any
fill rate by holding enough stock, so a single figure -- "97% fill" -- says
nothing at all without the stock it took. The sweep below runs every policy at
several safety settings and reports both axes at each. A policy is better only
if its curve sits above another's at the same inventory, and that is a claim a
reader can falsify by looking at one row.

**The incumbents are the real ones.** Not "no planning at all", but what an SME
actually runs:

| policy | what it stands for |
|---|---|
| `moving_average` | the spreadsheet: average the last twelve weeks, hold cover |
| `reorder_point` | the min/max fields in an ERP or accounting package, kept current |
| `reorder_point_stale` | the same fields, set once by someone who has since left |
| `naive_zero` | the policy a point-accuracy metric would choose |
| `forecast` | this product |

Every policy gets the same lead time, the same opening stock, the same lot
rounding and the same quantity of safety stock. The only thing that differs is
the demand signal each one acts on, which is the only thing under test.
"""

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import simulate  # noqa: E402
from planbrain.demo import populate  # noqa: E402
from planbrain.demo.generate import DemoDataset, Location, Part  # noqa: E402
from planbrain.facts.access import Fact  # noqa: E402
from planbrain.working_calendar import ALL_WEEKDAYS, SATURDAY, WorkingCalendar  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
TABLE = "fact_supply_demand"
MEASURE = "demand_actual"
LOC_ID = 1

#: Assumed, because a sales log does not record it. Fourteen days is an ordinary
#: replenishment lead time for an importing retailer.
#:
#: It is applied identically to every policy, so it cannot bias the comparison
#: -- it moves every curve together. What it does affect is the absolute service
#: level, which is why it is stated here rather than buried.
LEAD_TIME_DAYS = 14

#: Requested cycle service levels swept to trace the service/inventory curve.
#:
#: **Swept on the service level, not on days of cover, and that is a correction
#: to this harness rather than a preference.** In days-of-cover mode
#: `simulate.compare` gives the forecast and moving-average policies
#: `mean * days` of safety stock while the reorder point keeps its own textbook
#: one-sigma term, which does not move at all. The first run of this benchmark
#: reported `reorder_point` at exactly 77.2% and 132.5 units at every one of
#: five settings: it was not on the curve, and a moving policy compared against
#: a stationary one is not a comparison.
#:
#: In service-level mode every policy receives the same quantity --
#: z(alpha) * sigma * sqrt(L + R) -- so they move together and the only
#: difference left is the demand signal, which is the thing under test.
#:
#: 0.50 gives z = 0 and no safety stock at all, which is where the demand signal
#: is all a policy has and where the policies therefore differ most.
SERVICE_SWEEP = (0.50, 0.75, 0.90, 0.95, 0.99)

#: A series needs enough history to fit on and enough events to be a series
#: rather than a handful of accidents. Both committed before the run.
MIN_HISTORY_DAYS = 365
MIN_DEMAND_EVENTS = 12

#: Held out and never seen by any policy at fit time.
HOLDOUT_DAYS = 90


#: A weekday counts as trading if it carries at least this share of a normal
#: working day, measured as the median of the five busiest days. The two real
#: datasets sit nowhere near it -- the retailer's Sunday is 79% of a normal day
#: and the manufacturer's is 6% -- so the cutoff is a wide gap, not a knife
#: edge, and the same answer comes back anywhere between 20% and 33%.
TRADING_DAY_SHARE = 0.25


def trading_days(series: dict) -> frozenset:
    """Which weekdays this business actually trades on, read off its own data.

    **This was hardcoded to "every day except Saturday", which is the UK
    retailer's week.** The comment even said the calendar should come from the
    data, and warned why: the seasonal period is derived from it, so a wrong
    period puts every weekly pattern out of phase. Handed a manufacturer that
    works Monday to Friday, the hardcoded version called Sunday a trading day on
    the strength of 1.15% of its rows, and every weekly seasonality was fitted
    against a six-day week that does not exist.

    Counting demand *events* rather than units on purpose: one enormous order
    booked on a Sunday should not turn Sunday into a working day.
    """
    from collections import Counter
    from statistics import median

    events = Counter()
    for days in series.values():
        for day in days:
            events[day.weekday()] += 1
    if not events:
        raise ValueError("no demand rows, so no calendar can be derived")

    busiest = median(sorted(events.values(), reverse=True)[:5])
    trading = frozenset(d for d in ALL_WEEKDAYS if events[d] >= TRADING_DAY_SHARE * busiest)
    if not trading:
        raise ValueError("no weekday clears the trading-day cutoff")
    return trading


def load(data_dir: Path, *, limit: int = None, seed: int = 7):
    """Read the reduced CSVs into a dataset the planning engines already accept.

    Returns a `DemoDataset`, deliberately: the benchmark runs through exactly
    the same `simulate.compare` the product's own report uses. A bespoke
    benchmark harness would be free to differ from the shipped engine in ways
    nobody would notice, and the number would then be about the harness.
    """
    demand_path = data_dir / "daily_demand.csv"
    sku_path = data_dir / "skus.csv"
    for path in (demand_path, sku_path):
        if not path.exists():
            raise SystemExit(
                f"{path} not found -- run the fetch script first (see its "
                "--help; it lives outside the application boundary)"
            )

    prices = {}
    with sku_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            prices[row["sku"]] = float(row["unit_price"])

    series = defaultdict(dict)
    with demand_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            series[row["sku"]][date.fromisoformat(row["date"])] = float(row["qty"])

    if not series:
        raise SystemExit(f"{demand_path} holds no rows")

    every_day = [d for days in series.values() for d in days]
    history_start, history_end = min(every_day), max(every_day)
    span = (history_end - history_start).days + 1

    # Selection, stated: a SKU needs a year of shelf life and a dozen demand
    # events. Everything that clears the bar is kept -- no sampling by size,
    # which would quietly drop the intermittent half this product is for.
    eligible = sorted(
        code for code, days in series.items()
        if len(days) >= MIN_DEMAND_EVENTS
        and (max(days) - min(days)).days + 1 >= MIN_HISTORY_DAYS
        and code in prices
    )
    if limit:
        import random
        eligible = sorted(random.Random(seed).sample(eligible, min(limit, len(eligible))))

    sku_ids = {code: i + 1 for i, code in enumerate(eligible)}
    parts = [
        Part(sku_id=sku_ids[code], name=code, level="finished",
             lead_time_days=LEAD_TIME_DAYS, safety_stock=0.0,
             lot_policy="lot_for_lot", lot_qty=0.0, unit_cost=prices[code])
        for code in eligible
    ]

    facts = []
    for code in eligible:
        key = (sku_ids[code], LOC_ID)
        for day, qty in series[code].items():
            facts.append(Fact(keys=key, bucket_date=day, qty=qty))

    dataset = DemoDataset(
        locations=[Location(LOC_ID, "Warehouse", "plant")],
        parts=parts, bom=[], resources=[], routings=[], trucks=[],
        history_start=history_start, history_end=history_end,
        horizon_start=history_end + timedelta(days=1),
        horizon_end=history_end + timedelta(days=90),
        # Read off the data, not assumed. See `trading_days`.
        calendar=WorkingCalendar(trading_days(series)),
        facts={(TABLE, MEASURE): facts},
    )
    return dataset, {
        "stock_codes_in_file": len(series),
        "evaluated": len(eligible),
        "history_days": span,
        "history": f"{history_start} to {history_end}",
        "fact_rows": len(facts),
    }


def run(dataset, *, holdout_days=HOLDOUT_DAYS, sweep=SERVICE_SWEEP) -> list:
    """Replay the holdout at each safety setting. Returns a row per (policy, cover)."""
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    populate(con, dataset)

    rows = []
    for level in sweep:
        print(f"  replaying at {level:.0%} requested service...", flush=True)
        report = simulate.compare(
            con, dataset, holdout_days=holdout_days, safety_service_level=level,
        )
        for name, result in report["policies"].items():
            rows.append({
                "service_level": level,
                "policy": name,
                "fill_rate": result.fill_rate.value,
                "average_on_hand": result.average_on_hand.value,
                # The portfolio pair, beside the per-part pair. See
                # `PolicyResult.weighted_fill_rate`: they answer different
                # questions and are never merged.
                "weighted_fill_rate": result.weighted_fill_rate,
                "total_on_hand": result.total_on_hand,
                "units_short": result.units_short,
                "series": result.fill_rate.n_scored,
                "unscored": result.fill_rate.n_unscored,
                "inventory_value": (result.inventory_value.total
                                    if result.inventory_value else None),
                "by_pattern": {
                    pattern: (stats["fill_rate"].value,
                              stats["average_on_hand"].value,
                              stats["fill_rate"].n_scored)
                    for pattern, stats in result.by_pattern.items()
                },
            })
        rows[-1]["pattern_mix"] = report["pattern_mix"]
    con.close()
    return rows


def stock_to_match(curve, target_fill):
    """Average on-hand a policy needs to reach ``target_fill``, interpolated.

    ``curve`` is that policy's (fill_rate, average_on_hand) points, which are
    monotonic in practice because more safety stock buys more service.

    Returns ``(stock, status)``. Never extrapolates: a policy that does not
    span the fill rate being asked about has no answer here, and extending the
    last segment would manufacture exactly the comparison this exists to avoid.

    **The two ways of being off the curve are opposite and must not be merged.**
    A target above the curve means the incumbent never reaches that service at
    any setting tested. A target *below* it means the incumbent already beats
    that service at its cheapest setting -- which is a point against us, and
    reporting both as "never reaches it" would print an incumbent's win as its
    failure. The first draft of this did exactly that.
    """
    points = sorted(curve)
    if not points:
        return None, "no data"
    if target_fill > points[-1][0]:
        return None, "never reaches it"
    if target_fill < points[0][0]:
        return None, "already above it"
    for (f0, s0), (f1, s1) in zip(points, points[1:]):
        if f0 <= target_fill <= f1:
            if f1 == f0:
                return min(s0, s1), "ok"
            return s0 + (s1 - s0) * (target_fill - f0) / (f1 - f0), "ok"
    return points[-1][1], "ok"


def headline(rows, *, ours="forecast"):
    """For each of our settings, what the incumbents must hold to keep up.

    Inventory at equal service, rather than service at equal inventory. Both
    say the same thing about the same curves, but this is the direction a small
    manufacturer is answerable for: the cash is the constraint, and "the same
    service for less stock" is the sentence a finance manager can act on.
    """
    curves = {}
    for row in rows:
        curves.setdefault(row["policy"], []).append(
            (row["fill_rate"], row["average_on_hand"])
        )

    out = []
    for level, fill, stock in sorted(
        (r["service_level"], r["fill_rate"], r["average_on_hand"])
        for r in rows if r["policy"] == ours
    ):
        entry = {"service_level": level, "fill_rate": fill, "our_stock": stock,
                 "incumbents": {}}
        for policy, curve in curves.items():
            if policy == ours:
                continue
            needed, status = stock_to_match(curve, fill)
            entry["incumbents"][policy] = {
                "stock_needed": needed,
                "status": status,
                "extra_ratio": (needed / stock - 1.0) if needed and stock else None,
            }
        out.append(entry)
    return out


def render(rows, meta) -> str:
    """The report, as a table a reader can check one row of."""
    out = [
        "# Benchmark: real demand, real incumbents",
        "",
        f"- dataset: {meta['history']} ({meta['history_days']} days)",
        f"- stock codes in file: {meta['stock_codes_in_file']:,}",
        f"- series evaluated: {meta['evaluated']:,}"
        f" (>= {MIN_DEMAND_EVENTS} demand events and >= {MIN_HISTORY_DAYS} days of shelf life)",
        f"- holdout: last {HOLDOUT_DAYS} days, never seen at fit time",
        f"- lead time: {LEAD_TIME_DAYS} days, assumed, identical for every policy",
        "- safety stock: the same quantity for every policy at each setting,"
        " z(alpha) * sigma * sqrt(L + R)",
        "",
    ]
    mix = next((r.get("pattern_mix") for r in reversed(rows) if r.get("pattern_mix")), None)
    if mix:
        out += [f"- demand pattern mix: " +
                ", ".join(f"{k} {v}" for k, v in sorted(mix.items())), ""]

    by_level = defaultdict(list)
    for row in rows:
        by_level[row["service_level"]].append(row)

    for level in sorted(by_level):
        out += [f"## {level:.0%} requested cycle service", "",
                "| policy | fill rate achieved | avg on-hand (units) "
                "| working capital | units short |",
                "|---|---|---|---|---|"]
        for row in sorted(by_level[level], key=lambda r: -r["fill_rate"]):
            value = (f"{row['inventory_value']:,.0f}"
                     if row["inventory_value"] is not None else "--")
            out.append(
                f"| `{row['policy']}` | {row['fill_rate']:.1%} | "
                f"{row['average_on_hand']:,.1f} | {value} | "
                f"{row['units_short']:,.0f} |"
            )
        out.append("")

    # The headline, first, because it is the only line most readers will take
    # away and it should therefore be the one carrying its own caveats.
    out += ["## Stock needed to match this product's service", "",
            "Read one row: at the setting where Planning Brain fills *f* of demand",
            "holding *s* units, this is what each incumbent has to hold to fill the",
            "same *f*. Interpolated along each policy's own curve, never",
            "extrapolated. *never reaches it* means the incumbent does not get to",
            "that fill rate at any setting tested; *already above it* means the",
            "incumbent beats it at its cheapest setting, which is a point against",
            "us and is printed as such.", "",
            "| our fill rate | our stock | " +
            " | ".join(f"{p}" for p in sorted(
                {k for e in headline(rows) for k in e["incumbents"]})) + " |",
            "|---|---|" + "---|" * len({k for e in headline(rows)
                                        for k in e["incumbents"]})]
    for entry in headline(rows):
        cells = []
        for policy in sorted(entry["incumbents"]):
            stat = entry["incumbents"][policy]
            if stat["stock_needed"] is None:
                cells.append(stat["status"])
            else:
                cells.append(
                    f"{stat['stock_needed']:,.1f} ({stat['extra_ratio']:+.0%})"
                )
        out.append(
            f"| {entry['fill_rate']:.1%} | {entry['our_stock']:,.1f} | "
            + " | ".join(cells) + " |"
        )
    out.append("")

    # Per pattern, because that is where the claim is. The portfolio average is
    # dominated by whichever pattern happens to be most numerous, and this
    # product's positioning is explicitly about intermittent and lumpy demand --
    # not about beating a spreadsheet on a smooth fast mover, where a moving
    # average is genuinely hard to beat and we do not claim otherwise.
    patterns = sorted({
        pattern for row in rows for pattern in row["by_pattern"]
    })
    for pattern in patterns:
        sample = next(
            (row["by_pattern"][pattern][2] for row in rows
             if pattern in row["by_pattern"]), 0,
        )
        out += [f"## {pattern} demand ({sample:,} series)", "",
                "| policy | requested | fill rate | avg on-hand |",
                "|---|---|---|---|"]
        for row in sorted(rows, key=lambda r: (r["policy"], r["service_level"])):
            stats = row["by_pattern"].get(pattern)
            if stats is None or stats[0] is None:
                continue
            out.append(
                f"| `{row['policy']}` | {row['service_level']:.0%} | "
                f"{stats[0]:.1%} | {stats[1]:,.1f} |"
            )
        out.append("")

    # The frontier, which is the actual claim: what each policy delivers per
    # unit of stock it holds. A fill rate quoted without its inventory is
    # unfalsifiable, and a reader should be able to check the trade in one
    # place rather than by comparing five tables.
    out += ["## The frontier", "",
            "Fill rate against average on-hand, every setting of every policy.",
            "A policy is better only where its curve sits above another's at",
            "the same inventory.", "",
            "| policy | requested | fill rate | avg on-hand |",
            "|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: (r["policy"], r["service_level"])):
        out.append(
            f"| `{row['policy']}` | {row['service_level']:.0%} | "
            f"{row['fill_rate']:.1%} | {row['average_on_hand']:,.1f} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data/online-retail",
                        help="directory holding daily_demand.csv and skus.csv")
    parser.add_argument("--limit", type=int, default=None,
                        help="evaluate a random sample of this many series")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--out", default=None, help="write the report here")
    parser.add_argument("--json", default=None,
                        help="write the raw rows here, so the analysis can be "
                             "redone without replaying the portfolio")
    args = parser.parse_args(argv)

    dataset, meta = load(Path(args.data), limit=args.limit)
    for key, value in meta.items():
        print(f"{key:>22}: {value}")
    if not meta["evaluated"]:
        # An empty comparison must not print a clean, empty table.
        raise SystemExit("no series cleared the eligibility bar; nothing was compared")

    rows = run(dataset, holdout_days=args.holdout_days)
    report = render(rows, meta)
    print()
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"written to {args.out}")
    if args.json:
        import json
        Path(args.json).write_text(
            json.dumps({"meta": meta, "rows": rows}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"raw rows written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
