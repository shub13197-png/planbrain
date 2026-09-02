# forecast — statistical demand forecasting and its backtest

Build item 4. Fits a model per series, writes the `forecast` measure, and scores
itself against a seasonal naive baseline on held-out history.

## Layering

Same discipline as netreq. `metrics`, `classify` and `backtest` are pure and do
not import statsforecast — their tests run in 0.1s. `models` wraps statsforecast.
`__init__` is the only part that knows scenarios and dates exist.

## Model selection is per series, not per portfolio

| pattern | model | why |
|---|---|---|
| smooth, erratic | `AutoETS` | frequent demand with a weekly shape worth fitting |
| intermittent | `CrostonOptimized` | regular gaps, even sizes |
| lumpy | `TSB` | rare *and* variable; updates demand probability every bucket |
| unusable | seasonal naive | too few observations to fit anything honestly |

Classification is Syntetos–Boylan–Croston on ADI and CV², cutoffs 1.32 and 0.49.
One model over a mixed portfolio is the standard way to produce a respectable
average and a useless plan.

TSB for lumpy is partly an **obsolescence** choice: Croston never decays, so a
discontinued SKU forecasts its old rate forever. The demo carries 11 mid-history
discontinuations for exactly this reason.

Two adjustments on every model's output: **clamped at zero** (a negative
forecast nets backwards through MRP and manufactures supply), and **non-finite
output falls back to naive and is counted** (a NaN in `qty` would poison every
downstream sum in silence).

## MASE, and the two ways to get it wrong

Both are silent:

1. **Scaling on the test window.** The denominator comes from training only.
2. **The wrong seasonal period.** The demo plant is closed on Sunday. A one-step
   naive baseline is wrong every Saturday and Monday, which inflates the
   denominator and flatters every model that beats it. The default is **m=7**.

A perfectly periodic training window gives a zero scale factor, so MASE is
undefined. Those series are **counted as unscored, never dropped** — excluding
the hard ones without saying so is how a portfolio average gets improved.

## Results at seed 7, 40 of 242 series, 3 folds of 28 days

| | scored | mean | median | worse than 1.0 |
|---|---|---|---|---|
| chosen model | 40 | 1.066 | **0.908** | 16 |
| seasonal naive | 40 | 1.205 | 1.060 | 21 |

By pattern:

| pattern | scored | mean | median |
|---|---|---|---|
| erratic | 10 | **0.765** | 0.763 |
| smooth | 11 | **0.826** | 0.692 |
| intermittent | 10 | 1.338 | 1.096 |
| lumpy | 9 | 1.389 | 1.153 |

### The intermittent rows do not mean what they look like

Croston and TSB emit a **flat rate** — say 1.75 a day — against an actual that
is mostly zero with occasional spikes. Point-error metrics punish that hard,
while a naive forecast of zero scores well by being right on every quiet day and
wrong only where it matters.

**A MASE above 1.0 on intermittent SKUs does not mean the method is worse for
planning. It means MASE is measuring the wrong thing.** Croston-type methods
optimise expected inventory position over a lead time, not one-step point error.

Judging them properly needs fill rate and inventory held. **That is now built**
— see `docs/service-backtest.md`, which was promoted to build item 5 precisely
because this metric could not settle the question.

It settled it decisively. The forecast of zero that scores well on MASE for
intermittent demand delivers **70.6% fill on intermittent and 48.1% on lumpy**.
Accuracy was never the objective.

Do not quote the intermittent MASE as evidence either way. It is reported
because hiding a number that looks bad is worse than explaining it.

## The seam is now closed

`netreq.resolve_gross_req(source="forecast")` reads the `forecast` measure at
depot grain and reconciles **bottom-up** to plant level. The netting loop never
learns which source it got.

If the forecast measure is empty for the horizon, it **raises**. Every series
zero means nobody ran the forecast, not that demand is nil — and netting against
that would produce a confident, empty plan.

The per-depot lead-time limitation of bottom-up aggregation is a known gap,
documented in `docs/netreq.md`.

## Growth assumptions: the rules, committed before the code

**Nothing below has been implemented yet.** It is written first because a growth
overlay is the easiest way in this codebase to produce a number that is wrong and
looks right, and the rules are worth more decided in the cold than defended
afterwards.

### Two parameters, never one control

`demand_growth_pct` scales what customers are expected to take.
`capacity_growth_pct` scales what the plant can make. They are separate inputs
and neither defaults from the other.

The reason is that `rccp` exists to say when the plan does not fit. A single
"growth" control moving both sides together would report a comfortable factory
at every setting — demand +20% against capacity +20% is the most reassuring
possible wrong answer, and the one a planner is least likely to question.

### Annual rate, compounded daily, anchored at the last actual

    factor(bucket) = (1 + g) ** ((bucket - history_end).days / 365)

**Anchored at `history_end`, not `horizon_start`.** The horizon usually begins
some days after the last actual, and anchoring there would silently discard that
gap — the first planned bucket would carry no growth at all despite being weeks
past the level the history establishes. The error is small, always in the same
direction, and invisible.

365, not 365.25 and not the working calendar: growth is a business assumption
quoted per year, and a leap day is noise against a number someone typed to the
nearest percent.

### One source of trend, and the measurement that forced the rule

`AutoETS` selects among trend forms. On the seed-7 demo, of 103 smooth and
erratic series:

| selected form | count |
|---|---|
| `ETS(A,N,A)` — no trend | 82 |
| `ETS(A,A,A)` — additive trend | 11 |
| `ETS(A,Ad,A)` — damped trend | 10 |

**21 of 103 already carry a fitted trend.** Multiplying those by `(1+g)^t` grows
them twice. Croston and TSB carry no trend at all, so intermittent and lumpy
series would be grown exactly once. The portfolio would split into two
populations with different arithmetic applied to each, and nothing would fail.

**Rule: when `demand_growth_pct` is non-zero, AutoETS is fitted with the trend
term forced off (`model="ZNZ"`).** Then there is exactly one source of trend in
the plan and it is the number the planner typed. The run report states how many
series had a fitted trend suppressed, because that is a real cost — for those 21
series the model had inferred a trend from data and we discarded it in favour of
a portfolio-wide assumption.

Rejected: applying the overlay everywhere and reporting the overlap. It is
honest only if someone reads the report, and the two populations still get
different arithmetic. Rejected: overlaying only the flat-rate models, which
avoids the double count but makes the portfolio grow unevenly for a reason that
does not appear anywhere in the output.

### The kill condition, stated before the first run

**Growth of zero must produce byte-identical numbers to today's plan.** No
suppression, no overlay, no re-fit. If setting the parameter to zero changes any
published figure, the feature is wrong and does not ship — a planner who has not
opted into an assumption must not be silently given one.

That is testable, and it is the assertion written first.

### Where each parameter is applied, and why they differ

`forecast` applies demand growth before writing, because it *produces* that
series and owns it. `rccp` applies capacity growth when reading available hours,
because it does not own `fact_capacity` — that came from the system of record,
and rewriting someone else's actuals to encode our assumption would put an
assumption where a fact is supposed to be.

Both are read from the scenario rather than passed per call, so two engines
cannot run the same scenario under different assumptions. Comparing growth cases
is comparing peer scenarios, which is what the flat scenario model is for; there
is no second measure holding an un-grown copy.

