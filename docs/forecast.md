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

Judging them properly needs fill rate and inventory held, which is what the
multi-echelon simulation backtest is for — replay history, measure realised
service against stock carried. That is the honest proof-of-value report for this
half of the portfolio, and it is **not built yet**.

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
