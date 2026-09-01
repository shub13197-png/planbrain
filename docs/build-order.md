# Build order

The live plan. `docs/AGENT_SETUP.md` is the original brief and is left unedited
as the historical input — where the two disagree, this file wins.

| # | Item | State |
|---|---|---|
| 1 | Fact-table DDL + JSON contracts | done — `0cb538c`, `045c773` |
| 2 | Seeded demo dataset | done — `6a61025` |
| 3 | `netreq` — MRP explosion | done — `7339fc7` |
| 4 | Forecast — statsforecast + MASE backtest | done — `94971d6` |
| 5 | Service backtest — fill rate vs inventory | done — `2188fb8` |
| 6 | `rccp` — capacity load | done — `4015654` |
| 7 | Balance the demo, add drift, close the capacity loop | done — `eaa629b` |
| **8** | **Reconcile the two engines; add unit costs** | **done** |
| 9 | `haulplan` — fairness ledger + greedy assignment | done — `c18e5e5` |
| 10 | Fleet sized from the freight profile | done — `5704356` |
| **11** | **Fix the infeasibility framing + capacity sensitivity** | **done** |
| 12 | Importer | done — `f3819a1` |
| **13** | **README, demo, manual code audit** | **done** |
| 14 | UI grid | not started |
| 14 | Claim audit + published-figure pins | done — `3bbbfc4` |
| 15 | `docs/method.md` and the synthetic-world limit | done — `3e6ed4b` |
| 16 | Offline audit, socket guard, `--network=none` CI | done — `15d6987` |
| **17** | **Desktop packaging: Tauri shell + PyInstaller sidecar** | **done** |
| **18** | **M5 fetch, outside the app boundary** | **done** |
| **19** | **Manual column-mapping UI** | **done** |
| 20 | Column-mapping fixture corpus + accuracy threshold | next |
| 21 | Mapping model bake-off, on top of the manual UI | after |
| — | CLSP, DRP, Timefold | deferred with reason; see the docs for each |

## Change: the service backtest was promoted to item 5

Originally item 5 was `rccp` and the simulation backtest was an unnumbered
"proof-of-value report" in the reference-repo section. It moved ahead of `rccp`
and `haulplan` because:

1. **It was the blocker on any honest claim about intermittent SKUs**, which is
   roughly half the portfolio and the half where planning software earns its
   money.
2. **MASE proved it cannot substitute.** Item 4 measured intermittent demand at
   MASE 1.34 and lumpy at 1.39 — apparently worse than naive — while the naive
   forecast that scores well is one that never orders anything. A metric that
   rewards not ordering cannot judge an inventory policy.
3. **Service-level against inventory is the number that sells the tool.** A
   finance manager does not buy forecast accuracy. The whole positioning rests
   on this table, and nothing else we build matters as much.

`rccp` and `haulplan` each moved back one.

## Scope held deliberately narrow at item 5

* **Single echelon.** Multi-echelon later if ever.
* **Three policies compared**, no more: fitted forecast, naive-zero forecast,
  reorder point.
* **Output is one table** — fill rate against average on-hand per demand class.
  Treated as a deliverable, not a test artifact.

## Change: balancing and the capacity loop became item 7

`rccp` at item 6 reported the demo plan 3x over capacity, but the demo plant had
never been sized against its own demand — so the finding was about the generator
rather than about the plan. Balancing had to come before anything could be
concluded from it, and the capacity loop had to come before claim 2 could be
tested at all.

`haulplan` moved back to 8.

Scope, all in one pass:

* **Size the plant blind**, from demand, to a target committed in advance in
  `docs/capacity-sizing.md`.
* **Add demand drift**, which the item 5 stale-comparator finding needed in
  order to mean anything.
* **Close the capacity loop** with cost-based lot sizing, reusing the
  Wagner-Whitin DP already in `netreq`.
* **Fix the closed-day release bug** that `rccp` exposed at item 6.

## Change: reconciliation became item 8

Two engines disagreeing about the stock implied by the same plan is a
credibility problem, and it sat directly under the service table the positioning
rests on. It went ahead of `haulplan`, which moved to 9.

Scoped as **reconciliation, not unification**: the two engines *should* differ,
because one computes deterministic net requirements against a forecast and the
other replays realised demand with lost sales. The bug would be differing for
reasons nobody can name.

Unit costs came in the same item because the item 7 finding — 216 campaigns of a
quarter's supply each — was caused by valuing inventory in machine-hours, and
that distorted both engines.
