# How this was built

A working method for producing numbers you can defend. It was developed while
building a supply-chain planning tool, but nothing below depends on that — the
practices apply to any project whose output is a **measurement** rather than a
feature: a benchmark, a model evaluation, a performance claim, an A/B result, a
research prototype, a report someone will act on.

Every practice here exists because it caught something. Where a practice has a
cost, the cost is stated.

---

## The problem this method is for

Ordinary software fails loudly. A crash, a failing test, a 500 — something tells
you.

Measurement work fails **quietly**. The pipeline runs, a number appears, the
number is plausible, and it is wrong. Nothing raises. The tests pass, because
the code does what it was written to do. You find out when someone acts on it,
or when a reader checks and you cannot explain the discrepancy.

Almost everything below is a way of converting a quiet failure into a loud one.

---

## 1. Commit the rule before the run, in its own commit

Before any run that will produce a headline number, write down the rule, the
parameters and the thresholds. **Commit that alone**, then implement, then run,
then append the outcome.

Not a comment in the code. Not the same commit as the implementation. A separate
commit, so the ordering is verifiable in `git log` by anyone.

**Why:** a rule written after seeing the result is indistinguishable from a rule
fitted to it — including to yourself, six weeks later. The commit boundary is
the only durable evidence that it was not.

**It changed the outcome three times here.** A capacity target fixed in advance
meant a system could not be quietly resized until its plan looked feasible; it
came out failing and was published failing. A cost parameter declared explicitly
out of bounds meant an absurd result had to be diagnosed rather than tuned away
— and the diagnosis turned out to be a missing input, which tuning would have
buried permanently.

**Cost:** an extra commit, and the discipline to write the rule while you still
do not know the answer. That second part is the hard one.

## 2. State the kill condition before you have the evidence

Alongside the rule, write down **what result would make you drop the claim**.

**Why:** a claim that survived a stated kill condition is worth far more than one
that was never at risk, and the difference is invisible after the fact unless you
wrote it down.

Here, one claim rested on an effect that had not yet been tested. The commitment,
written first, was that it would be **dropped rather than softened** if a fair
test showed nothing. The test showed something and the claim stands — and the
README says it was scheduled for removal, because that is the part that makes it
credible.

**Cost:** occasionally you have to drop a claim you liked.

## 3. Try to break your own check before you report it

For every consistency check you plan to present as evidence, ask: **what change
to the system would make this fail?** Then inject that change and confirm it
does, and that the check moves in proportion.

**Why:** a check that survives no attack is documentation, not verification. It
looks rigorous. It has an assertion. It passes. And it constrains nothing.

**The instance:** a reconciliation reported a residual of exactly −0.0 across
four named terms — apparently a strong result. It was worthless. Each term was
defined as a difference between adjacent steps of a ladder, so they summed to
the total as an **algebraic identity**. Feeding it five random numbers produced
the same zero. No injected error could move it, because perturbing a step
changed both sides equally and they cancelled.

The replacement was a **cross-implementation** check: the same quantity computed
by two independently written pieces of code. That one moves proportionally with
an injected error, catches a one-step misalignment, and had already caught a
real bug nobody had noticed.

**Prefer checks with two independent paths to the same number** — separate
implementations, stored against recomputed, a published constant against a
derivation. If both sides come from the same computation, you are testing that
arithmetic is arithmetic.

**Cost:** you will sometimes discover your best-looking evidence is empty. That
is the practice working.

## 3b. State where the thing you are guarding actually lives

For every guard, write down **where the thing it guards lives**, then confirm
the guard looks there. A guard that runs somewhere the subject does not exist
cannot fire, and it will be reported as coverage.

This is a distinct failure from a check that *can* fire but is weak. These pass
cleanly, forever, and their passing is evidence of nothing.

**Four instances on this project, all the same shape:**

* A reconciliation summed four terms and reported a residual of −0.0. The terms
  were differences between adjacent rungs, so they summed to the gap by algebra.
  The check lived in a place where no error could exist.
* A test suite passed on every commit and had **never run in the packaged
  build**. It verified a checkout; the thing shipped was an executable, and the
  first time it ran there it could not even import its own helper module.
* A bundle gate held `requests` on a forbidden list and walked only the
  filesystem. PyInstaller archives pure-Python packages *inside the executable*,
  so `requests` was in the one place the gate could not see. Half of it was
  decorative.
* A register pinned every published figure — in three of eleven documents. The
  proof-of-value report was not among them, and had been contradicting the
  README for weeks.

**The question to ask** is not "does this check pass?" but "if the thing I fear
were present, would this check be looking at the place it would be?" Write the
answer down next to the guard. Then sweep the others once, because if you got
it wrong here you probably got it wrong somewhere else.

## 4. Retract in place, and leave the retraction visible

When a published claim turns out to be wrong, correct it **where it was
published**, and say what changed and why. Do not quietly overwrite it.

**The instance:** a claim that the tool excelled on one category of input was
measured on a 40-item sample and asserted for a 222-item portfolio. On the full
run the comparison **inverted** — the simple baseline won. The README now carries
the corrected table and a short section titled *What changed when we stopped
sampling*.

**Why leave it visible:** a reader who finds a silent correction stops trusting
everything else. A reader who finds a documented retraction has just been shown
your error-correction working, which is the only real evidence that other numbers
on the page were checked.

**Cost:** your documentation contains your mistakes. This is a feature.

## 5. Audit what each claim rests on — and hardest when it saves you work

For every claim, ask whether the number comes from a **sample**, a **single
seed**, or a **single scenario**. Re-run across several. Report the range, not
just the mean, and how many runs the claim actually holds on.

Here, one claim died at 0 of 5 seeds (confirming an earlier retraction), four
held at 5 of 5, and one **held at only 3 of 5**.

**The important refinement:** apply this hardest to numbers that let you **skip
work**. The 3-of-5 result was a measurement showing an optimiser had almost no
room to improve — which had been accepted as settling whether to build one. It
came from a single run. Across five, the headroom was up to ten times larger.

A number that justifies *not* doing something attracts less scrutiny precisely
because it is convenient. That is exactly backwards. **Convenience is a reason
for more checking, not less.**

**Cost:** re-running across seeds is slow. Do it for published claims, not for
every intermediate.

## 6. Keep the negative results in

When a comparison goes against you, publish it at the same prominence as the
ones that do not.

Four are kept here: the simple baseline wins on one input category; the tool is
close to a tie overall; the main pipeline output is infeasible under a
constraint it does not model; and a diagnostic explains less than a tenth of the
problem it appeared to explain.

**Why:** a document showing only wins tells a reader nothing, because they cannot
tell whether the losses were measured or omitted. A document showing where the
thing is beaten is the only kind whose wins mean anything.

## 7. Pins protect claims; tests protect code

They are different jobs and you need both.

A test suite asserts **behaviour** — correctly. That is precisely why it cannot
notice a published figure going stale: the behaviour did not change. Only the
number did.

**The instance:** removing one call to a random-number generator shifted a
deterministic stream. The dataset was still reproducible; it was simply a
*different* dataset. Four hundred and forty-nine tests passed. Every figure in
the documentation had been computed from the old one and was now wrong, and a
reader running the demo would have seen numbers contradicting the docs with no
way to tell which was right.

A **pin** is a register of every published figure holding its value, the exact
literal string as it appears in the prose, and which files it appears in. Two
CI-enforced links:

1. **register → code** — the value matches a fresh computation.
2. **register → prose** — the literal string is actually present in each file.

Break either and the build fails. Changing a published number then costs a
deliberate edit to the register, which is the point: **a claim should not be able
to change by accident.**

**Cost:** the register has to be maintained, and the code-side check is the
slowest thing in the suite because it recomputes everything. Both are cheaper
than a reader finding the discrepancy.

## 8. Guard every result whose empty case looks like success

Hunt for operations where "nothing happened" is indistinguishable from "it
worked". They fail *upward*, into a passing state.

The catalogue found here, each of which had shipped:

| operation | what the empty case looked like |
|---|---|
| glob matching no files | a CI gate reporting "clean" |
| a code-generation step losing its input | a valid file containing `null` |
| a config lookup returning a default | a validator silently skipping its most important check |
| a string replacement finding no match | an edit script printing success, having changed nothing |
| a lookup default masking a missing key | a whole category of cost silently priced at zero |

**The distinction that makes this judgement rather than a rule:** is absence
*meaningful* or *unexpected*? An item with no sales, a bucket with no rows, a
resource with no work — absence there is real and the default encodes it.
Hardening those would turn ordinary sparsity into an error. Fix only the cases
where absence means something went wrong.

## 9. Register your constants, and record what must agree with what

Keep one page listing every number that was **chosen rather than derived**, which
piece of work set it, and which document justifies it. End it with a table of
**consistency requirements** — pairs of constants that have to agree — and where
each is checked.

**Why:** constants committed at different times, by rules unaware of each other,
collide. It happened twice here. Once helpfully: two independently derived
figures landed close enough to be real internal-consistency evidence. Once
unhelpfully: a fleet's vehicle sizes and a separately chosen load size collided
so that half the fleet could not carry a standard load. **Both were found only by
running something.** The register exists so the third is found by reading.

**Cost:** one page, kept current. Trivial against the alternative.

## 10. Assert the boundary, not the count

When writing a guard, encode the **rule's boundary**, not a count of the things
it governs.

An early guard here asserted an exemption list had at most four entries. It broke
the moment a legitimate fifth was added, and would have been renegotiated every
time it fired — and a guard that gets edited whenever it fires protects nothing.
It was replaced by the actual rule: *no module that computes a result may ever be
exempt*. That one stays silent through legitimate growth and fires only on the
thing that matters.

## 11. Name your ceiling

State plainly what your evidence **cannot** establish, at the same prominence as
what it can — not in a gaps list at the bottom.

The ceiling here: every number was measured against a synthetic world we built
ourselves. Five seeds test sensitivity to sampling *within one model*. They do
not test whether the model resembles reality. A claim holding on 5 of 5 seeds is
evidence against a fluke and is **not** evidence about anyone else's data.

**Why at the top:** a reader will find the limit eventually. Finding it stated
plainly builds confidence; finding it themselves destroys it.

---

## The guard sweep

Every guard in this repository, and where the thing it guards actually lives:

| guard | what it guards | does it look where that lives? |
|---|---|---|
| `offline.engage()` | outbound connections | **Yes, partially.** Covers all pure-Python clients; native code and subprocesses are named as uncovered and are why the container check exists too |
| `--network=none` on the packaged artifact | the shipped binary calling out | **Yes.** Runs the actual artifact, after this failed once by running a checkout instead |
| `check_fact_access.py` | direct fact-table SQL | **Yes.** Scans every `.py` in the tree; a search for fact-table names in `.js`, `.html`, `.yaml` and `.sql` found only the DDL itself |
| `bundle_manifest.py` | unexpected dependencies | **Yes, now.** Filesystem *and* the PYZ archive. It looked at only the filesystem until this sweep's predecessor |
| `test_dataset_boundary` | app code reaching `datasets/` | **Yes.** Checks imports *and* subprocess calls in `planbrain/` and `tools/`, and asserts the spec cannot reach it |
| `test_encoding` | non-UTF-8 files | **Yes.** Walks every text suffix in the repo |
| `test_no_silent_defaults` | masking defaults | **Partially.** Covers the sites found in one sweep; it is a snapshot, not a rule that catches new ones |
| **published-figure pins** | figures in prose | **No — this sweep found it.** Three of eleven documents |

The last row was live: `docs/service-backtest.md`, the proof-of-value report,
carried pre-resize numbers from a 40-series sample while the README carried
corrected full-portfolio ones. Two documents in one repository answering the
same question differently, for weeks, with a green build.

## What this method costs

Honestly: roughly a third more time, spent on things that produce no features.
Extra commits, re-runs across seeds, a register to maintain, documentation
containing your own mistakes, and the recurring experience of discovering that
your best evidence was empty.

## What it buys

A reader can check you. Every threshold was committed before its run and the
order is in the history. Every published figure fails the build if it drifts.
Every claim states how many runs it holds on. The retractions are still there.

That is a different kind of asset from a good result. Good results are cheap and
unverifiable. **A verifiable method is what makes a result worth reading**, and
it is the part that transfers to the next project.

## What it does not do

It does not make the work correct. It makes incorrectness *findable* — usually by
you, before it matters, and occasionally by a reader afterwards, which is the
system working rather than failing.

It also cannot tell you that a thing you built models the wrong problem. Every
practice here checks that a measurement is what it claims to be. None of them
checks that the measurement was worth taking. That judgement stays yours.
