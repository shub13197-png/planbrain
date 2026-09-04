"""Fetch UCI Online Retail II and reduce it to daily demand. **Not the application.**

    python datasets/fetch_online_retail.py --out data/online-retail

This is the no-login dataset `datasets/README.md` names as the fallback when the
Kaggle account M5 requires is too much friction. It needs no credentials, no
competition rules to accept, and no token: one HTTPS GET from the UCI Machine
Learning Repository.

Same boundary rules as `fetch_m5.py` and for the same reason (`docs/offline.md`):
this directory is never imported or invoked by application code, never ships in
the bundle, and is run by a developer deliberately. A developer downloading a
public dataset is not the application reaching the network.

## What it is, and why this one

Online Retail II is the transaction log of a UK online gift retailer, 1 December
2009 to 9 December 2011 — roughly a million invoice lines over about 5000 stock
codes. It is **real demand from a real small business**, which is the whole
point: every service figure this project publishes has so far come from a
generated dataset, and `docs/method.md` names that synthetic-world ceiling at
the top of the page.

It is also the right *shape* for the claim being tested. A gift retailer's SKU
sells in bursts with long silences between them, which is intermittent and lumpy
demand — the pattern this product exists for and the pattern a moving average in
a spreadsheet handles worst.

**Licence:** CC BY 4.0, so redistribution would be permitted. We fetch anyway,
because 45 MB of xlsx does not belong in a git history.

**Citation:** Chen, D. (2019). *Online Retail II* [Dataset]. UCI Machine
Learning Repository. https://doi.org/10.24432/C5CG6D

## What comes out

| file | contents |
|---|---|
| `daily_demand.csv` | `sku,date,qty` — one row per stock code per day it sold |
| `skus.csv` | `sku,description,unit_price` — the part master, priced from the data |

Both are inputs to `python -m tools.benchmark`, which is inside the boundary and
reads a path it is given.
"""

import argparse
import csv
import hashlib
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from statistics import median

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"

#: Pinned so a silently changed upstream file fails here rather than moving a
#: published benchmark number with nothing noticing. Recorded from the fetch on
#: 2026-09-05; if it changes, that is a finding to investigate, not a constant
#: to update reflexively.
SHA256 = "572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb"

MEMBER = "online_retail_II.xlsx"

#: Stock codes that are not products. The dataset mixes postage, bank charges,
#: manual adjustments and test rows into the same column as gift items, and
#: leaving them in would put "demand for POSTAGE" into a forecast comparison.
#:
#: Matched exactly, not by prefix: `D` is a discount line and `DOT` is postage,
#: but `DCGS0003` is a real product and a prefix rule would eat it.
NOT_A_PRODUCT = {
    "POST", "DOT", "C2", "C3", "M", "m", "D", "S", "B", "BANK CHARGES",
    "AMAZONFEE", "CRUK", "PADS", "TEST001", "TEST002", "ADJUST", "ADJUST2",
    "gift_0001_10", "gift_0001_20", "gift_0001_30", "gift_0001_40",
    "gift_0001_50", "SP1002",
}

#: A real stock code is five digits, optionally with a letter suffix. Anything
#: else is a manual entry, and the ones above are only the codes that recur.
PRODUCT_CODE = re.compile(r"^\d{5}[A-Za-z]?$")


def fetch(out: Path, *, verify_only: bool = False) -> Path:
    """Download the archive, or verify the one already there."""
    archive = out / "online_retail_ii.zip"
    if not archive.exists():
        if verify_only:
            raise SystemExit(f"nothing to verify: {archive} does not exist")
        out.mkdir(parents=True, exist_ok=True)
        print(f"fetching {URL}")
        urllib.request.urlretrieve(URL, archive)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != SHA256:
        raise SystemExit(
            f"checksum mismatch for {archive}\n"
            f"  expected {SHA256}\n"
            f"  got      {digest}\n"
            "The upstream file changed. Every benchmark figure derived from it "
            "is now describing a different dataset -- investigate before "
            "updating this constant."
        )
    print(f"verified {archive} ({archive.stat().st_size:,} bytes)")
    return archive


def rows(archive: Path):
    """Yield (sku, date, qty, price) for every line that is real demand.

    Read-only and row-streamed: the workbook is a million rows across two
    sheets, and loading it whole costs several gigabytes.
    """
    from openpyxl import load_workbook

    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xlsx")]
        if MEMBER not in names:
            raise SystemExit(
                f"{MEMBER} is not in the archive; found {names}. The upstream "
                "layout changed and this script would otherwise read nothing "
                "and report success."
            )
        extracted = archive.parent / MEMBER
        if not extracted.exists():
            extracted.write_bytes(zf.read(MEMBER))

    book = load_workbook(extracted, read_only=True, data_only=True)
    for sheet in book.worksheets:
        stream = sheet.iter_rows(values_only=True)
        header = [str(h).strip() if h else "" for h in next(stream)]
        index = {name: i for i, name in enumerate(header)}
        for name in ("Invoice", "StockCode", "Quantity", "InvoiceDate", "Price"):
            if name not in index:
                raise SystemExit(f"{sheet.title}: no {name!r} column in {header}")

        for row in stream:
            invoice = row[index["Invoice"]]
            code = row[index["StockCode"]]
            qty = row[index["Quantity"]]
            when = row[index["InvoiceDate"]]
            price = row[index["Price"]]
            if code is None or qty is None or when is None:
                continue
            code = str(code).strip()
            # A 'C' invoice is a credit note -- a return, not demand. Negative
            # quantities elsewhere are adjustments. Both are excluded rather
            # than netted off: a return in December is not negative demand in
            # December, it is a reversal of a sale in November.
            if str(invoice).upper().startswith("C") or qty <= 0:
                continue
            if code in NOT_A_PRODUCT or not PRODUCT_CODE.match(code):
                continue
            if price is None or price <= 0:
                continue
            yield code, when.date(), int(qty), float(price)
    book.close()


def reduce_to_daily(archive: Path, out: Path) -> dict:
    """Collapse invoice lines to one row per stock code per day."""
    demand = defaultdict(int)
    prices = defaultdict(list)
    lines = 0

    for code, day, qty, price in rows(archive):
        demand[(code, day)] += qty
        prices[code].append(price)
        lines += 1
        if lines % 200_000 == 0:
            print(f"  {lines:,} lines")

    if not demand:
        # An empty result must not read as success: a changed sheet name or a
        # filter that matched everything would otherwise write two valid, empty
        # files and report a clean run.
        raise SystemExit(
            "no demand rows survived the filters; the layout or the stock code "
            "convention changed upstream"
        )

    demand_path = out / "daily_demand.csv"
    with demand_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sku", "date", "qty"])
        for (code, day), qty in sorted(demand.items()):
            writer.writerow([code, day.isoformat(), qty])

    sku_path = out / "skus.csv"
    with sku_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sku", "unit_price"])
        for code in sorted(prices):
            # Median, not mean: the price column carries promotional lines and
            # the occasional 0.01 adjustment, and a mean would drag the part
            # master toward whichever of those a SKU happened to have.
            writer.writerow([code, round(median(prices[code]), 4)])

    days = {day for _, day in demand}
    summary = {
        "invoice_lines": lines,
        "skus": len(prices),
        "sku_days": len(demand),
        "first_day": min(days).isoformat(),
        "last_day": max(days).isoformat(),
        "daily_demand": str(demand_path),
        "skus_file": str(sku_path),
    }
    for key, value in summary.items():
        print(f"{key:>14}: {value}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/online-retail")
    parser.add_argument("--verify-only", action="store_true",
                        help="check the archive's checksum and stop")
    args = parser.parse_args(argv)

    out = Path(args.out)
    archive = fetch(out, verify_only=args.verify_only)
    if args.verify_only:
        return 0
    reduce_to_daily(archive, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
