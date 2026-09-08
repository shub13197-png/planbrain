"""A manufacturer's order book, reduced to the two CSVs the benchmark reads.

    python datasets/prepare_product_demand.py --out data/manufacturing

**Why a second dataset exists at all.** Every service figure this project has
published came from one real source: UCI Online Retail II, a UK *online
retailer*. `docs/status.md` names that as the largest hole in the evidence --
the product is sold to small *manufacturers*, and one dataset from the wrong
industry settles nothing. This is the second source, and it is a manufacturer:
historical order demand for a global manufacturing company, thousands of
products in dozens of categories across four central warehouses, 2011-2017.

Source: "Forecasts for Product Demand" (Kaggle, felixzhao), mirrored in a
public GitHub repository because Kaggle needs an authenticated client and this
script must run without credentials.

    https://raw.githubusercontent.com/premanand09/
    product-demand-forecasting-using-ARIMA/master/Historical%20Product%20Demand.csv

**This file lives outside the application boundary on purpose.** It knows about
a URL and a vendor's column names; `tools/benchmark.py` knows only about a
directory holding `daily_demand.csv` and `skus.csv`. `tests/test_dataset_
boundary.py` enforces the split, and has caught it being crossed before.

Three judgement calls, stated because each of them moves the number:

1.  **A SKU is a product at a warehouse**, not a product. The file carries four
    warehouses, and summing them would smooth exactly the intermittency this
    product exists to plan. Aggregating would flatter the forecast; keeping the
    grain is the harder test and is what a planner actually holds stock for.
2.  **Returns are dropped, not netted.** Negative order demand is written in the
    source as `(1000)`. A return is not demand, and netting it into the day's
    figure would understate what had to be on the shelf that morning.
3.  **There are no prices in this dataset.** Every unit price is written as 1,
    so the benchmark's `inventory_value` column is *in units, not currency* for
    this source. The comparison that matters -- fill rate against units on hand
    -- is unaffected, because it never reads the price.
"""

import argparse
import csv
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

URL = ("https://raw.githubusercontent.com/premanand09/"
       "product-demand-forecasting-using-ARIMA/master/Historical%20Product%20Demand.csv")
SOURCE_NAME = "historical_product_demand.csv"


def fetch(out: Path) -> Path:
    raw = out / SOURCE_NAME
    if raw.exists():
        print(f"already have {raw} ({raw.stat().st_size:,} bytes)")
        return raw
    out.mkdir(parents=True, exist_ok=True)
    print(f"fetching {URL}")
    urllib.request.urlretrieve(URL, raw)
    print(f"wrote {raw} ({raw.stat().st_size:,} bytes)")
    return raw


def parse_qty(text: str):
    """`(1000)` is a return. Returns are not demand; see the module docstring."""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_day(text: str):
    """The source writes `2012/1/10`, which is not ISO and not zero-padded."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        y, m, d = (int(part) for part in text.split("/"))
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def reduce(raw: Path, out: Path) -> dict:
    series = defaultdict(float)
    skipped_date = skipped_qty = 0
    total = 0

    with raw.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            total += 1
            day = parse_day(row.get("Date"))
            if day is None:
                skipped_date += 1
                continue
            qty = parse_qty(row.get("Order_Demand"))
            if qty is None:
                skipped_qty += 1
                continue
            # Product at a warehouse. See judgement call 1.
            sku = f"{row['Product_Code'].strip()}@{row['Warehouse'].strip()}"
            series[(sku, day)] += qty

    if not series:
        raise SystemExit(f"{raw} produced no demand rows; the format has changed")

    out.mkdir(parents=True, exist_ok=True)
    demand_path = out / "daily_demand.csv"
    with demand_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sku", "date", "qty"])
        for (sku, day), qty in sorted(series.items()):
            writer.writerow([sku, day.isoformat(), qty])

    codes = sorted({sku for sku, _ in series})
    sku_path = out / "skus.csv"
    with sku_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sku", "unit_price"])
        for sku in codes:
            # 1, not a guess. See judgement call 3.
            writer.writerow([sku, 1])

    days = [day for _, day in series]
    report = {
        "source_rows": total,
        "skipped_no_date": skipped_date,
        "skipped_return_or_zero": skipped_qty,
        "sku_locations": len(codes),
        "demand_rows": len(series),
        "history": f"{min(days).isoformat()} to {max(days).isoformat()}",
        "daily_demand": str(demand_path),
        "skus": str(sku_path),
        "unit_price": "1 for every SKU -- this source carries no prices",
    }
    for key, value in report.items():
        print(f"{key:>24}: {value}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="data/manufacturing")
    args = parser.parse_args(argv)
    out = Path(args.out)
    reduce(fetch(out), out)
    print("\nnow run: python -m tools.benchmark --data " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
