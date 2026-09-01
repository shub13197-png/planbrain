# Column mapping

The manual path for getting a customer's spreadsheet into Planning Brain.
**Built first and built to stand alone**, because the mapping model will sit on
top of it: an assistant that is the *only* way to import fails whenever it is
wrong, and nobody can tell when it is wrong without a manual path to compare
against.

## The flow

1. **Choose a file and a sheet.** Workbooks that open on a `Summary` tab with
   the data on `Sheet2` are common, and picking the wrong sheet is the fastest
   route to a confidently wrong import.
2. **Check the detected header.** The app says which row it chose, how confident
   it is, and *why* — a wrong header row shifts every column by one and produces
   a file that imports cleanly and means nothing. The row is editable.
3. **Map the columns.** For each field the system needs, pick which of your
   columns supplies it. Required fields are marked; obvious matches are
   pre-filled.
4. **Preview.** Twenty parsed rows, plus any warnings. This is the centre of the
   feature.
5. **Import.** All or nothing.
6. **Save a profile** so the next file from the same source is one click.

## The Excel realities it handles

Each of these has actually bitten someone:

| reality | what happens |
|---|---|
| **Title rows above the header** | Company name, report title, date range, blank — skipped, and the count is reported |
| **Two-row headers** | `Material` merged across two columns with `Item Code` and `Description` beneath becomes `Material Item Code`, `Material Description` |
| **Merged cells** | openpyxl returns a value only for a merge's top-left cell; the value is filled across the range, so a header merged over three columns does not lose two names |
| **Leading zeros** | `007821` reaches the mapping as text, not as the float `7821.0` |
| **Unit suffixes** | `Qty (Kgs)`, `QTY_KG` and `Quantity` all match the `qty` field. The heading you see stays exactly as you wrote it |
| **Trailing whitespace** | `"Item Code "` matches `item_code`; Excel leaves non-breaking spaces behind too |
| **Semicolon CSVs** | European exports, which nobody mentions |
| **Blank spacer rows** | Skipped, not reported as errors |

## The warning that matters most

`sku_id` is an **integer** everywhere in this system — the fact tables, the BOM,
the routings. So material `007821` is stored as `7821`, and the mapping cannot
change that: it is a schema-wide decision, not a column-mapping one.

Usually harmless. If a file is internally consistent, `007821` and `7821` are the
same part throughout. **It is not harmless if a catalogue contains both as
distinct materials**, which happens after a migration.

Only the user knows which, so the preview says so plainly rather than deciding:

> **sku_id** ← Material Item Code: `007821 -> 7821`
> leading zeros are dropped because 'sku_id' is a whole number here. Harmless if
> your file uses one style throughout; a problem if '007821' and '7821' are
> different parts in your catalogue.

## Suggestions are deliberately not clever

Only normalised equality and a small alias table — the kind of match a user would
be annoyed to make by hand. Anything requiring judgement is **left blank**.

A plausible wrong guess accepted without reading is worse than an empty dropdown,
because nobody checks a field that already looks filled in. That restraint is the
whole reason the manual path can be trusted as a baseline for the model.

Two rules keep it honest:

* **A merged group label does not break a match.** `Material Item Code` is still
  an item code — the specific part of a merged heading is its tail.
* **One column cannot supply two fields.** Without that, a file with both `Date`
  and `Posting Date` maps both to `bucket_date` and silently drops one.

## Profiles are data

A profile is a YAML file. A user can write one in a text editor without this
application, and a SAP or Tally profile is just a file we ship.

```yaml
name: Tally sales register
table: history
source_hint: tally
header_row: 4          # counting from 1, as Excel shows it
columns:
  sku_id: Item Code
  loc_id: Godown
  bucket_date: Date
  qty: Quantity (Kgs)
```

Left side is the field Planning Brain needs; right side is the heading in *your*
file, spelled exactly. `header_row` is 1-based because someone hand-editing it
is counting rows on screen.

Loaded with `yaml.safe_load` only. Plain `yaml.load` executes arbitrary Python
from the document, and a profile is exactly the kind of file that gets emailed
around.

A malformed profile is **reported, not skipped** — silently ignoring it means a
hand-written profile never appears and the user has nothing to debug from.

## The baseline is a strong incumbent, and the model may not beat it

**Written before any model result exists**, so that the conclusion cannot be
rationalised after seeing one.

Measured on the 45-case corpus, the dumb normalised-equality matcher scores
**69.0% accuracy on the holdout with 9.1% false confidence — and zero wrong
columns.** On 84 decisions it never once pointed at the wrong column; it simply
had no answer 25 times.

That is a genuinely strong incumbent, and specifically strong in the dimension
that matters. **Its failure mode is silence, not error.** A blank dropdown makes
a user look; a confidently wrong one does not, because nobody re-checks a field
that already appears filled in. The matcher already satisfies the
false-confidence threshold the model must clear, so a model has to be *both*
more accurate *and* no more reckless — and fluency pushes against the second.

**So the model may well fail to clear the bar. If it does, that is the result,
not a setback.**

The honest outcome in that case is **a 160 MB application that maps columns
conservatively and asks when unsure** — not a 2.6 GB one that guesses. For a
user on a rural connection, which is the user this product exists for, 2.5 GB of
weights is a real cost, and it has to buy something. Ten points of accuracy at
no extra recklessness would buy it. A rounding error would not.

Recording this now because the temptation after a disappointing bake-off is to
find a reason the threshold was too strict. The threshold was set before the
baseline was scored, and the baseline turning out strong is a reason to be
pleased with the fallback, not a reason to move the bar.

## What is not built

* **A file picker.** The path is typed. The Tauri dialog plugin is the obvious
  next step and is not in this pass.
* **Writing anything but history.** Reference data — parts, BOM, routings, fleet
  — maps and previews, but only `history` is written to facts. That boundary
  belongs to the system of record.
* **Any model.** On purpose. The corpus and the bake-off come next, and this has
  to be trustworthy first.
