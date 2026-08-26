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
| **6** | **`rccp` — capacity load** | **done** |
| 7 | `haulplan` — fairness ledger, then Timefold | not started |
| 8 | Importer | not started |
| 9 | UI grid | not started |

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
