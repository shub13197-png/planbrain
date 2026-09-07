# Status

**Where the work stands, and what is next.** Updated at the end of each working
session. `docs/decisions.md` is the permanent record of *why*; this file is the
short answer to *where are we*, and it is the first thing to read when picking
the work back up.

Last updated: **2026-09-06**, at `1302619`. The suite passes locally.

## See it

The interface, published and openable from anywhere:
<https://claude.ai/code/artifact/2fb531c9-5bdc-400b-8b86-8790942732ee>

The real `index.html` replaying real engine output, in a working state: worked
example loaded, plan run, one quantity overridden by hand. Regenerate it with
`tools/render_ui.py` (below) after any interface change.

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
| Persistence | One SQLite file per user, at the path `docs/install.md` documents. A request is a transaction, and the part master is stored beside the facts, so **closing the window and reopening it returns you to your data** — verified through the packaged binary, not only in a test. |
| Packaging | Two Windows installers — the standard one embeds the WebView2 bootstrapper (~150 MB, inside budget), the `-offline` variant embeds the runtime (~330 MB, no network at install). |

## Where it stands against the alternatives

`docs/benchmark.md` — the service/inventory frontier on **real** demand (UCI
Online Retail II, 2,947 stock codes, 90-day holdout) against the tools a small
manufacturer actually has: a twelve-week moving average in a spreadsheet, an
ERP's min/max fields kept current, and the same fields set once and never
revisited.

Read that file rather than quoting a single number from it. Any policy can buy
any fill rate with enough stock, so the comparison is a curve.

## Blocked, not merely open

**GitHub Actions will not start any job on this account.**

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

The `ci` run at 09:07 on 6 September passed; everything from 09:28 is refused
before a step runs — which looks exactly like four platforms failing at once and
is not that. **Nothing merges to a verified state until this is cleared**, and
item 1 below depends on it.

## Open, in the order it matters

1. **The interface has never worked in a shipped build, and the fix is not yet
   verified.** `withGlobalTauri` was absent from `tauri.conf.json`, so
   `window.__TAURI__` did not exist and the frontend threw on its first line —
   **every button in the window dead, on every launch, from the first one.**
   Fixed and asserted in `tests/test_desktop_shell.py`; it needs one CI release
   build, a download, an install, and a click to confirm. Blocked on the billing
   item above.

   **The 2026-09-04 diagnosis of that screen was wrong and was published as
   fact**; retracted in `docs/decisions.md`. The race it described is real, is
   fixed, and was not what anyone was looking at.

2. **Three Windows builds were lost to shell details**, each costing a full
   four-platform run: a `"//"` key Tauri's strict config schema rejects,
   PowerShell stripping the quotes out of an inline `--config` JSON, and word
   splitting on the space in `Planning Brain_0.1.0_…`. All three are fixed and
   the last was verified locally against files with spaces. The two-installer
   path has therefore **never completed a green run** — that is what the next
   release build settles, along with item 1.

3. **The Linux AppImage fails the size gate at 161.5 MB against 150**, and is
   left failing on purpose. There is no bloat — the sidecar is accounted for to
   the megabyte and every part of it is required by `statsforecast`. The budget
   simply is not achievable on Linux with this dependency set. Trimming scipy
   with PyInstaller excludes, dropping a dependency, or re-deriving the budget
   from the measurement are the options; the third is probably right and is
   still a decision. `docs/packaging.md`.

4. **The second dataset does not confirm the first, and it is the one from the
   right industry.** A manufacturer's order book now runs through the same
   benchmark (646 product-warehouse series, 2011-2017,
   `datasets/prepare_product_demand.py`). Read at matched stock, the margin over
   a well-kept ERP min/max is **-1.4 to +0.6 points on its lumpy series -- a
   tie** -- against +1.5 to +3.7 on the retailer, and the stale-parameter
   advantage that dominated the retail run has only two overlapping points here.
   **The headline claim currently rests on one dataset from an industry this
   product is not sold to.** A third source is no longer optional.
   `docs/decisions.md`, *A second dataset, and it does not confirm the first*.

   The retail figures themselves are sound: the run reproduced **exactly** from
   the 45 MB source -- 100 cells, zero drift -- which is the check CI cannot do
   and nothing had ever done.

   **Since measured and largely answered.** The gap was the safety-stock rule,
   not the forecast: it targeted a cycle service level under a normal
   distribution while the benchmark scored fill rate on demand that is 93-96%
   lumpy. `order_up_to_for_fill_rate` inverts the fill-rate identity against the
   observed distribution instead, and on the manufacturer it **leads both real
   incumbents at every overlapping stock level above ~8,900 units** -- up to
   +9.7 points on the product's own previous policy. It loses on the retailer,
   where weekly structure is real and a forecast earns its place, so it ships as
   a policy rather than a replacement and the per-series selection rule is the
   open work. `docs/decisions.md`, *Safety stock was answering the wrong
   question*.
5. **Single echelon.** The plan answers *what must the plant make*, not *what
   must each depot hold*. DRP is deferred, and it is the largest gap in the
   README's list.

## Running it without a Rust toolchain

```bash
pip install -e .
planbrain demo        # build the worked example into your own database
planbrain plan        # run it
planbrain orders      # the list to act on
planbrain where       # which file your data is in
```

`planbrain.cli` exists because `pip install` previously produced a library and
no way to plan with it: the backend is a stdio server for the desktop shell, and
everything else lived in `tools/`, outside the shipped package. It is not a
second interface to maintain -- the window is still where a planner works -- it
is what makes the product runnable on a machine with Python and no Rust.

The standalone backend also builds and runs here:
`python -m PyInstaller packaging/backend.spec` produces
`build/dist/planbrain-backend/` at 161 MB against a 400 MB budget, reports
`"offline": true` in its handshake, and answers `plan.run` on a database written
by a previous process.

## How to see it without a Rust toolchain

```bash
python -m tools.render_ui --out build/demo --with-override --screenshot
python -m tools.render_ui --out build/demo --import-tab --name import.html --screenshot
```

Renders the real `index.html` against **real replies from the real engines**,
with `window.__TAURI__` stubbed. `--with-override` fixes a real quantity through
the real API first, so the *numbers you fixed* screen renders populated — and
then shows the feature's consequences rather than the feature: capping a batch
at 12,000 puts a 792-unit shortage on the risk screen and pushes a replacement
order into the list.

It is the only thing in the repository that exercises the interface's rendering
path. The shell tests check that the page parses and calls methods that exist,
which is not the same as it drawing the right thing — and looking at a render is
how the `[hidden]` bug was found, where an author `display: flex` outranked the
browser's `[hidden] { display: none }` and left the sheet picker on screen
before a file had been chosen.

**It also masked a worse bug for a release.** The harness defines
`window.__TAURI__` in order to stub it, so it rendered the application perfectly
while the shipped one was inert. It now refuses to run unless the config would
have provided that global for real.

## Verified end to end, on real data

2,947 stock codes from a real transaction log, planned in **9.5 s**; 460,811
demand rows written in 2.5 s; 59,625 planned releases read in 3.4 s. Two product
faults that the 222-series demo could not reach were found and fixed doing it —
a query-size ceiling at ~500 keys, and a risk screen that could never fire.

## Standing rules that are easy to lose

* **Launch the artefact, then press a button.** The window opening proves the
  shell started and nothing more; everything interactive is downstream of code
  that may never have run. An installed build rendered perfectly for two days
  with every control dead.
* **Never let a stub supply the thing whose absence is the bug.** A harness that
  defines what production is missing cannot find what production is missing.
* **Published figures need a pin.** `tools/published.py` links every quoted
  number to a fresh computation *and* to the prose it appears in. Tests protect
  code; pins protect claims.
* **An empty result must not read as success.** A gate that measured nothing, an
  export with no rows, a shortage screen with nothing on it — each has to say
  which of the two states it is in.
* **When a check rejects your work, fix the work.** If the check itself has to
  change, the reason goes in `docs/decisions.md` where a reader can be
  suspicious of it.
