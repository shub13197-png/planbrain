# Project: Planning Brain

## What this is
An open-source supply chain PLANNING layer for small manufacturers who cannot
afford SAP / o9 / Kinaxis. It sits on top of their existing system of record.

## Hard scope boundaries — refuse work outside these
- NO order management, ATP, or allocation
- NO warehouse execution (picking, putaway, bins)
- NO accounting, costing, GST, or e-way bills
- NO transport execution (dispatch, driver app, ePOD, GPS)
- We PLAN. We do not TRANSACT.
If a task implies any of the above, stop and say so instead of building it.

## Architecture rules — non-negotiable
1. InvenTree core is READ-ONLY. Plugin apps only. Never edit core files.
2. Planning time-series lives in dedicated fact tables keyed
   (sku_id, loc_id, bucket_date, measure, scenario_id) — never in
   InvenTree custom-object tables.
3. Solvers run behind a queue, never in the request cycle. Hard time limit,
   return best-so-far.
4. LLM/agent code NEVER computes plan numbers. It reads, explains, and proposes
   parameters. Net requirements, capacity load, and truck assignment are
   deterministic Python. Always.
5. Licence gate: MIT / Apache-2.0 / BSD dependencies only. No AGPL, no GPL.

## Definition of done — every task
- [ ] Unit tests pass
- [ ] At least one test asserts against a known-correct textbook fixture
      (stockpyl built-in instances have published answers — use them)
- [ ] No new dependency without justification in the PR body
- [ ] Public functions have docstrings naming the algorithm and its source
- [ ] `/ponytail-review` run on the diff, over-engineering removed

## Working loop — follow this for every task, in order

### 1. ORIENT
Read CLAUDE.md and query Graphiti for prior decisions on this area.
State in one line what you believe the current state is.
If it contradicts what I just asked for, say so BEFORE writing code.

### 2. PLAN
Write the plan as 3-7 numbered steps. Name the files you will touch.
Name the test you will write first.
STOP. Wait for my approval. Do not code yet.

### 3. TEST FIRST
Write the failing test. Run it. Show me it fails for the right reason.

### 4. MINIMUM CODE
Apply the ponytail ladder before writing anything:
does it need to exist → does the codebase already have it → stdlib →
existing dependency → one line → minimum code.
Make the test pass. Nothing more.

### 5. VERIFY
Run the full suite. Run /ponytail-review on the diff.
For any change to auth, upload, or a solver endpoint: run Strix locally.

### 6. RECORD
Write to Graphiti: what changed, what was decided, what was explicitly
rejected and why. Rejections matter more than decisions — they stop me
relitigating the same choice in three weeks.

### 7. REPORT
One paragraph: what changed, what I should look at, what is now unblocked.
Then STOP. Do not start the next task.

## When you are stuck
Do not guess and do not invent a plausible number. Say what is ambiguous
and give me two options with trade-offs. A wrong planning number that looks
right is the worst possible output of this project.
