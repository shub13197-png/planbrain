# Status

**Where the work stands, and what is next.** Updated at the end of each working
session. `docs/decisions.md` is the permanent record of *why*; this file is the
short answer to *where are we*, and it is the first thing to read when picking
the work back up.

Last updated: **2026-09-06**

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
| **Overrides** | The textbook firm planned order: fix a quantity on the day material is needed, with a name and a reason. No run resizes or reschedules it, and a shortfall shows as a shortage rather than being topped back up. |
| Persistence | One SQLite file per user, at the path `docs/install.md` documents. |
| Packaging | Two Windows installers — the standard one embeds the WebView2 bootstrapper (~150 MB, inside budget), the `-offline` variant embeds the runtime (~330 MB, no network at install). |

## Where it stands against the alternatives

`docs/benchmark.md` — the service/inventory frontier on **real** demand (UCI
Online Retail II, 2,947 stock codes, 90-day holdout) against the tools a small
manufacturer actually has: a twelve-week moving average in a spreadsheet, an
ERP's min/max fields kept current, and the same fields set once and never
revisited.

Read that file rather than quoting a single number from it. Any policy can buy
any fill rate with enough stock, so the comparison is a curve.

## Open, in the order it matters

1. **The interface has never actually worked in a shipped build, and the fix
   is not yet verified.** `withGlobalTauri` was absent from `tauri.conf.json`,
   so `window.__TAURI__` did not exist and the frontend threw on its first
   line — every button dead, on every launch. Fixed, asserted in
   `tests/test_desktop_shell.py`, and it needs one more CI release build to
   confirm on a real install. **The 2026-09-04 diagnosis of that screen was
   wrong and is retracted in `docs/decisions.md`.**
2. **The Linux AppImage fails the size gate at 161.5 MB against 150**, and is
   left failing on purpose. There is no bloat — the sidecar is accounted for to
   the megabyte and every part of it is required by `statsforecast`. The budget
   simply is not achievable on Linux with this dependency set. Trimming scipy
   with PyInstaller excludes, dropping a dependency, or re-deriving the budget
   from the measurement are the options; the third is probably right and is
   still a decision. `docs/packaging.md`.
4. **Only one real dataset.** `docs/benchmark.md` disagrees with the README's
   own synthetic retraction about lumpy demand. One dataset settles nothing;
   what would is a second and third, ideally from manufacturing rather than
   retail. The figures themselves reproduced exactly on a re-run and are gated
   against the committed run in CI (`tests/test_benchmark_claims.py`); what CI
   cannot check is whether the engines still *produce* that run, which needs the
   45 MB download.
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
