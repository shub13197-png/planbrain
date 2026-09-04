# Status

**Where the work stands, and what is next.** Updated at the end of each working
session. `docs/decisions.md` is the permanent record of *why*; this file is the
short answer to *where are we*, and it is the first thing to read when picking
the work back up.

Last updated: **2026-09-05**

## What the product does today

| | |
|---|---|
| Import | `.xlsx`, `.xlsm`, `.csv`. Column mapping with a confirm step; nothing is written until a preview passes. Ships SAP MB51 and Tally sales-register profiles. |
| Forecast | Per-series model selection — AutoETS for frequent demand, Croston/TSB for intermittent — with demand-pattern classification. |
| Plan | Time-phased MRP: gross requirement → net of stock and open orders → lot-sized → offset by lead time. Wagner-Whitin cross-checked against stockpyl's published instances. |
| Capacity | Rough-cut load against available hours, per resource per day, with an `--explain` attribution. Detects and explains; does not fix. |
| **Orders** | The planned releases as a list a planner acts on, exportable to `.xlsx`/`.csv`. |
| **Risks** | Two lists, never one total: orders already overdue — the signal that actually fires, **212 overdue orders across 159 items** on the demo — and buckets where the projected balance goes negative. |
| Desktop | Tauri shell, PyInstaller backend, stdio IPC, offline guard engaged in-process. |
| Persistence | One SQLite file per user, at the path `docs/install.md` documents. |

## Where it stands against the alternatives

`docs/benchmark.md` — the service/inventory frontier on **real** demand (UCI
Online Retail II, 2,947 stock codes, 90-day holdout) against the tools a small
manufacturer actually has: a twelve-week moving average in a spreadsheet, an
ERP's min/max fields kept current, and the same fields set once and never
revisited.

Read that file rather than quoting a single number from it. Any policy can buy
any fill rate with enough stock, so the comparison is a curve.

## Open, in the order it matters

1. **The launch fix is not itself launched.** The dropped-first-line bug is
   fixed and cannot be compiled here — no Rust toolchain, no MSVC. It needs a CI
   release build, then download, extract, launch, and confirm the status bar
   reads *Offline*. Until that happens the fix is unverified.
2. **The Windows installer is 330 MB against a committed 150 MB budget.** The
   gate fails the build, correctly. The cause is the bundled WebView2 offline
   runtime, which exists to keep the "no network during install" promise. That
   is a product decision and it is open: ship two installers, switch to the
   bootstrapper, or move the budget with a written reason.
3. **Overrides are designed and not built** — `docs/overrides.md`. The textbook
   firm planned order, with provenance beside the fact rather than inside it.
   Adds to the measure vocabulary, which is a contract, so the design is up for
   review before code.
4. **Only one real dataset.** `docs/benchmark.md` disagrees with the README's
   own synthetic retraction about lumpy demand. One dataset settles nothing;
   what would is a second and third, ideally from manufacturing rather than
   retail. The benchmark figures are also not CI-pinned — the dataset is a 45 MB
   download CI does not have — which is a weaker guarantee than everything else
   here and is stated in the document.
5. **Single echelon.** The plan answers *what must the plant make*, not *what
   must each depot hold*. DRP is deferred, and it is the largest gap in the
   README's list.

## How to see it without a Rust toolchain

```bash
python -m tools.render_ui --out build/ui --screenshot
```

Renders the real `index.html` against **real replies from the real engines**,
with `window.__TAURI__` stubbed. It is the only thing in the repository that
exercises the interface's rendering path — the shell tests check that the page
parses and calls methods that exist, which is not the same as it drawing the
right thing.

## Verified end to end, on real data

2,947 stock codes from a real transaction log, planned in **9.5 s**; 460,811
demand rows written in 2.5 s; 59,625 planned releases read in 3.4 s. Two product
faults that the 222-series demo could not reach were found and fixed doing it —
a query-size ceiling at ~500 keys, and a risk screen that could never fire.

## Standing rules that are easy to lose

* **Launch the artefact.** Cross-file rules cannot see a race, and three faults
  in one sitting came out of running the built application once.
* **Published figures need a pin.** `tools/published.py` links every quoted
  number to a fresh computation *and* to the prose it appears in. Tests protect
  code; pins protect claims.
* **An empty result must not read as success.** A gate that measured nothing, an
  export with no rows, a shortage screen with nothing on it — each has to say
  which of the two states it is in.
* **When a check rejects your work, fix the work.** If the check itself has to
  change, the reason goes in `docs/decisions.md` where a reader can be
  suspicious of it.
