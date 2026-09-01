"""Fetch the M5 competition dataset from Kaggle. **Not part of the application.**

    python datasets/fetch_m5.py --out data/m5

This directory sits outside the app boundary on purpose, per `docs/offline.md`:

* It is **never imported or invoked by application code.** Nothing under
  `planbrain/` references it, and `tests/test_offline.py` asserts that.
* It is **excluded from the packaged bundle**, so it cannot ship and cannot run
  on a customer's machine.
* It is run **by a developer, deliberately, once**, before the app is started.

A user choosing to download a public dataset is not the application reaching the
network. That distinction is the whole reason this file lives here rather than
in `tools/`, which is inside the boundary.

## Why credentials are required, and why that is not a flaw we hid

M5 is hosted on Kaggle behind an account, and its competition rules **restrict
redistribution** — so a derived extract checked into this repository would be a
licensing problem, not merely a size one. That rules out shipping our own copy.

You therefore need your own Kaggle account and an API token, which this script
reads and never stores, transmits or logs. If the account requirement proves
too much friction, the fallback is a genuinely public dataset — never a
redistributed M5 extract.

## Getting a token

1. kaggle.com → your profile → Settings → API → **Create New Token**
2. Save `kaggle.json` to `~/.kaggle/kaggle.json`, or set `KAGGLE_USERNAME` and
   `KAGGLE_KEY`
3. Accept the competition rules once, on the competition page — the API cannot
   do this for you and the download fails with 403 until you have

The download is roughly 450 MB compressed.
"""

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

COMPETITION = "m5-forecasting-accuracy"

#: Files the competition ships, and what each is for. Named rather than
#: globbed so a changed competition layout fails loudly instead of quietly
#: producing a partial dataset.
EXPECTED = {
    "sales_train_validation.csv": "daily unit sales per item per store",
    "sales_train_evaluation.csv": "the same, extended by 28 days",
    "calendar.csv": "dates, weekdays, SNAP days and events",
    "sell_prices.csv": "weekly price per item per store",
    "sample_submission.csv": "the submission format",
}


def have_credentials() -> bool:
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    return (Path.home() / ".kaggle" / "kaggle.json").exists()


def explain_credentials() -> None:
    print(
        "No Kaggle credentials found.\n"
        "\n"
        "  1. kaggle.com -> profile -> Settings -> API -> Create New Token\n"
        f"  2. save kaggle.json to {Path.home() / '.kaggle' / 'kaggle.json'}\n"
        "     (or set KAGGLE_USERNAME and KAGGLE_KEY)\n"
        f"  3. accept the rules at kaggle.com/c/{COMPETITION}/rules\n"
        "\n"
        "This script never stores, logs or transmits your token -- it hands it\n"
        "to the Kaggle CLI, which is the only thing here that touches the\n"
        "network. See docs/offline.md for why this is outside the app boundary.",
        file=sys.stderr,
    )


def download(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    print(f"downloading {COMPETITION} (~450 MB) to {out} ...")
    result = subprocess.run(
        [sys.executable, "-m", "kaggle", "competitions", "download",
         "-c", COMPETITION, "-p", str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "403" in stderr or "Forbidden" in stderr:
            print(
                f"Kaggle returned 403. This nearly always means the competition\n"
                f"rules have not been accepted for your account. Visit\n"
                f"  https://www.kaggle.com/c/{COMPETITION}/rules\n"
                f"and accept, then re-run.", file=sys.stderr,
            )
        elif "No module named" in stderr:
            print("the Kaggle CLI is not installed: pip install kaggle",
                  file=sys.stderr)
        else:
            print(stderr[-800:], file=sys.stderr)
        return 1
    return 0


def extract(out: Path) -> int:
    archives = list(out.glob("*.zip"))
    if not archives:
        # An empty glob would otherwise let this report success having done
        # nothing -- the failure mode this project keeps finding.
        print(f"no .zip found in {out}; the download did not produce one",
              file=sys.stderr)
        return 1
    for archive in archives:
        print(f"extracting {archive.name} ...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(out)
    return 0


def verify(out: Path) -> int:
    """Every expected file present and non-empty.

    Named files rather than a glob, so a changed competition layout fails here
    instead of producing a partial dataset that looks fine until a forecast is
    quietly fitted to two thirds of the history.
    """
    problems = []
    for name, purpose in EXPECTED.items():
        path = out / name
        if not path.exists():
            problems.append(f"missing {name} ({purpose})")
        elif path.stat().st_size == 0:
            problems.append(f"{name} is empty")

    if problems:
        print("the download is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    total = sum((out / n).stat().st_size for n in EXPECTED)
    print(f"\nverified {len(EXPECTED)} files, {total / 1e6:,.0f} MB in {out}")
    for name in EXPECTED:
        print(f"  {(out / name).stat().st_size / 1e6:8.1f} MB  {name}")
    print(
        "\nThis data stays where you put it. Nothing in the application reads\n"
        "this directory, and the packaged app cannot -- it is outside the\n"
        "bundle by construction."
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/m5"))
    parser.add_argument("--verify-only", action="store_true",
                        help="check an existing download without fetching")
    args = parser.parse_args(argv)

    if args.verify_only:
        return verify(args.out)
    if not have_credentials():
        explain_credentials()
        return 2
    if download(args.out) or extract(args.out):
        return 1
    return verify(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
