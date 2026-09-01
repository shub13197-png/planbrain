# datasets/ — outside the application boundary

**Nothing in this directory is part of the application.** It holds scripts a
developer runs deliberately, once, to fetch public datasets for evaluation.

The distinction matters and is enforced rather than asserted:

| property | how it is enforced |
|---|---|
| Never imported by application code | `tests/test_dataset_boundary.py` |
| Never invoked by `planbrain/` or `tools/` | same |
| Excluded from the packaged bundle | `.dockerignore`, and the bundle identity gate would flag it |
| Reaches the network | **yes, deliberately** — that is why it is out here |

`docs/offline.md` states the principle: *a user choosing to download a public
dataset is not the application reaching the network.* This directory is where
that distinction is kept.

## What is here

| script | dataset | licence note |
|---|---|---|
| `fetch_m5.py` | M5 Forecasting — Accuracy (Kaggle) | competition rules **restrict redistribution**, so we fetch rather than ship a derived copy |

## Using it

```bash
pip install kaggle
python datasets/fetch_m5.py --out data/m5
python datasets/fetch_m5.py --out data/m5 --verify-only
```

You need your own Kaggle account and API token, and you must accept the
competition rules once on its page — the API cannot do that for you and returns
403 until you have. The script never stores, logs or transmits your token.

**If the account requirement is too much friction**, the fallback is a genuinely
public dataset with no login. It is *never* a redistributed M5 extract, which
would be a licensing problem rather than a convenience.

## Unverified

**The fetch path itself is unverified.** The no-credentials path, the
verification logic and the boundary enforcement are all tested; the actual
Kaggle download has never run here, because there are no credentials in this
environment.

So the first real run may surface a changed competition layout, a renamed file,
or an API change. `EXPECTED` names files rather than globbing precisely so that
fails loudly — but "fails loudly" is itself a prediction until someone runs it.

## What this is for

M5 is real retail demand at scale: 30,490 series, five years, heavily
intermittent. The demo dataset is synthetic and every claim measured on it
carries that ceiling —
[the README says so at the top](../README.md). Real data is the only thing that
lifts it.

**M5 is not the source for the column-mapping fixtures.** Its headers are tidy
competition CSVs; the mapping problem is merged cells, multi-row headers,
transliterated names and unit suffixes. That corpus is hand-authored and lives
with the mapping work.
