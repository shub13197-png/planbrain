# Planning Brain — Repo Stack & Claude Code Setup

Scope lock: **a planning layer that sits on top of an existing system of record.**
Not an ERP. Not a WMS. Not accounting. If a feature request implies replacing
Tally / Zoho / the customer's ERP, the answer is "we import from it."

---

## 1. Locked repo stack

### Foundation
| Repo | Purpose | Licence |
|---|---|---|
| `inventree/InvenTree` | Master data: parts, BOM/recipes, stock, locations, POs, auth, plugin system, REST+OpenAPI | MIT |

Plugin apps only. **Never patch InvenTree core** — the moment you do, upgrades stop.

### Planning engines
| Repo | Fills | Licence |
|---|---|---|
| `Nixtla/statsforecast` | Stat forecasting — AutoETS, AutoARIMA, Croston/TSB/IMAPA for intermittent demand | Apache-2.0 |
| `Nixtla/hierarchicalforecast` | Reconciliation so SKU forecasts tie to plant and national totals | check LICENSE |
| `LarrySnyder/stockpyl` | Inventory policy — EOQ, newsvendor, Wagner–Whitin lot sizing, safety stock, multi-echelon | MIT |
| `PyJobShop/PyJobShop` | Campaign sequencing with sequence-dependent setup times (grade changeover / flush) | check LICENSE |
| `TimefoldAI/timefold-solver` | Truck long-haul fairness — has a native load-balancing constraint collector | Apache-2.0 |
| `VROOM-Project/pyvroom` + OSRM | Real road distance/duration matrix feeding the fairness solver | BSD |

### Reference only — read, do not fork
| Repo | Why |
|---|---|
| `frePPLe/frepple` | Closest functional analogue. Study its forecast-netting and capacity-report models. AngularJS frontend, C++ solver — do not fork. |
| `anshul-musing/multi-echelon-inventory-optimization` | SimPy backtest-harness pattern: replay history, measure realised fill rate vs inventory. This is your proof-of-value report. |
| `TimefoldAI/timefold-quickstarts` | VRP + employee-scheduling models to crib constraint structure from |

Licence check: MIT + Apache-2.0 + BSD throughout. No AGPL anywhere — deliberate,
so a hosted tier stays possible later.

### Your code (the actual differentiator)
- `netreq` — time-phased MRP: gross → on-hand → open POs → net → lot-size → lead-time offset
- `rccp` — rough-cut capacity: load vs available hours per resource per bucket
- `haulplan` — fairness ledger (cumulative long-haul km per truck YTD) + Timefold assignment
- `importer` — Excel/CSV/Tally/Zoho ingest with row-level validation errors

---

## 2. Claude Code skill set

### Install first
```bash
# Code discipline — stops over-engineering, the #1 failure mode on solo agent builds
/plugin marketplace add DietrichGebert/ponytail

# Anthropic first-party
/plugin install frontend-design        # design quality, breaks "AI slop" defaults
```

### Agent memory — Graphiti MCP
`getzep/graphiti` — temporal knowledge graph with bi-temporal edges (when a fact
became true, when it stopped being true). Correct choice here because your
architecture decisions *supersede* each other across a long build; a flat vector
store keeps resurfacing decisions you already reversed.

```bash
claude mcp add graphiti -- docker run -i --rm \
  -e NEO4J_URI -e OPENAI_API_KEY getzep/graphiti-mcp
```

Seed it on day one with the scope lock, the licence policy, and the
"plugins-only, never patch core" rule. Those are the constraints an agent
will otherwise quietly violate around week three.

### Security — Strix
`usestrix/strix` (MIT) — autonomous pentest agents that validate findings with
working PoCs rather than static-analysis false positives. Ships SKILL.md-compatible
skills for Claude Code.

```bash
pipx install strix-agent
strix --target ./planbrain
```

Run against **your own local instance only.** Gate it in CI on PRs touching
auth, the importer, or any solver endpoint — file upload and long-running
solve endpoints are your two real attack surfaces.

### UI/UX
| Skill | Role |
|---|---|
| `anthropics/skills` → `frontend-design` | Creative direction. First-party. |
| `vercel-labs/agent-skills` → `web-design-guidelines` | Audit gate — 100+ accessibility/performance/UX rules |
| `vercel-labs/agent-skills` → React best practices | Perf rules for the planning grid |

Pair them: one for taste, one for correctness. For a planning grid, correctness
matters far more than aesthetics — planners live in dense tables all day.

### Workflow
- `obra/superpowers` — subagent-per-task with TDD enforcement and a review gate.
  Worth the setup cost precisely because planning bugs don't crash, they
  produce confidently wrong numbers.

### Security caution on third-party skills
A SKILL.md is executable instruction text and any bundled script runs with your
agent's permissions. Snyk's 2026 audit found a substantial share of catalogued
skills carried injection or overreach issues. **Read every SKILL.md before
installing.** Prefer first-party (Anthropic, Vercel) and repos with real star
history. This applies to everything above.

---

## 3. The looping prompt

Put this in `CLAUDE.md` at repo root.

```markdown
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
```

---

## 4. Build order

1. Fact-table DDL + JSON contracts between Django and solver — before any engine code
2. Seeded demo dataset: fake blending plant, 200 SKUs, 18 months messy history, 12 trucks
3. `netreq` — MRP explosion, tested against textbook fixtures
4. Forecast — statsforecast with a backtest harness reporting MASE
5. `rccp` — capacity load bars
6. `haulplan` — fairness ledger, then Timefold on top
7. Importer
8. UI grid — last, once the math is trustworthy

Ship 1–3 before touching anything else. A thin vertical slice is demoable at
week four; three complete horizontal layers are demoable never.
