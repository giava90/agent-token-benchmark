# Next experiments

> **Phase 2 completed 2026-09-28.** Batch probe, multi-item grouping, error
> analysis and the full-scale cost model are all done — see `PHASE2` below for
> what they settled and what changed as a result. Items 2.1, 2.2 and 3.2 in the
> original backlog are now answered.

## PHASE 2 RESULTS

| Experiment | Cost | Outcome |
|---|---|---|
| Batch API probe | $0.21 | **Batch is a loss here.** 2 of 10 requests read the cache, 8 wrote it at the 1h premium. $0.0207/item vs $0.00325 live cached — 6x worse, not half. |
| Multi-item grouping | $0.49 | **70–76% cheaper at identical accuracy.** Haiku 81.7% both ways, Opus-low 93.3% both ways, McNemar p=1.000 on both. Biggest lever found. |
| Error analysis | free | Only 1/117 items failed by all five; Jaccard overlap 0.10–0.40. Majority voting loses to the best single arm. Failed positives are disproportionately multi-citation claims — a benchmark limitation, not evidence about the review. |
| Full-scale model | free | All 608 claims + full bibliography = 184k tokens, **fits in one context**. Chunking saves 59% but the saving is context *scoping*, not delegation; an orchestrator on top adds $0.11 and buys nothing. |
| Agent slice test | plan usage | **Void.** Agent scored 24/24 by detecting that labels alternated with item index. Benchmark flaw, now fixed. |

### The benchmark flaw, and what it cost

`make_positive = (i % 2 == 0)` made every label predictable from position — all
117 items alternated. Single-call runs could not exploit it (one item per
request); grouped runs scored 81.7%/93.3%, matching one-at-a-time, so they did
not either. **The published cost and accuracy findings stand.** The agent result
does not. Labels now come from a seeded shuffle (52% alternation); the original
construction is kept as `data/pairs_v1_alternating.jsonl` for provenance.

**Required before any further quality comparison:** re-run the benchmark on the
corrected pairs. Cost and caching results do not depend on label ordering and
need no re-run.

---


Backlog after the first paid run (2026-09-28, $8.66, five configurations over
117 claims). Results in `data/results.json`; analysis in `scripts/08_analyse.py`.

## Where the project actually stands

The project set out with two learning objectives. **Only the first has been
touched**, and only partly:

| Objective | Status |
|---|---|
| 1. Correct use of memory — avoiding costly context reloads | **Partly done.** Caching measured end to end (7.9x prefix ratio, 71–88% of every bill). But the idle-and-reload scenario that motivated the objective was never run. |
| 2. Task and sub-task breakdown for agents | **Untested.** No orchestrator, no subagents, no decomposition. |

And the thing actually measured was narrower than the original design: a
**single-call classification** over a warm cached prefix. No agent loop, no
growing context, no tool results, no idle gaps. Architectures A, B and C from
`project-context.md` all remain unrun.

---

## Priority 1 — cheap, settles a published claim

### 1.1 Replicate the Sonnet effort comparison
**Question:** is the Sonnet default-vs-low difference real?
**Why:** raw p = 0.035, but Holm-Bonferroni across six comparisons puts it at
p = 0.105. The post currently calls it suggestive and unconfirmed. The original
plan called for repeat runs; this study has none.
**Method:** three more runs each of `sonnet-default` and `sonnet-low`, same seed
corpus. Compare across replicates rather than within one.
**Cost:** ~$7. **Settles:** whether to keep or retract the claim.

### 1.2 Capture the reasoning behind the Opus reversal
**Question:** *why* did more thinking hurt?
**Why:** the over-reasoning story is currently a hypothesis. The run logged
verdicts but not reply text, so it cannot be checked after the fact.
**Method:** re-run the 9 discordant items on `opus-default` and `opus-low` with
reply text persisted, and read the stated reasons. Consider
`thinking.display: "summarized"` to see the reasoning itself rather than only
the one-line justification.
**Cost:** ~$0.80. **Settles:** whether the mechanism claim survives.

---

## Priority 2 — untested levers that change the cost model

### 2.1 Batch API
**Question:** does the flat 50% discount stack with caching in practice, or does
async batching forfeit the cache reads that dominate the bill?
**Why it matters more than it sounds:** batch is single-shot and submitted all at
once, so the **send-one-then-fan-out pattern is impossible** — nothing can wait
for a first token. The docs describe cache hits inside a concurrent batch as
best-effort. Since 71–88% of cost is cache reads, losing them would swamp a 50%
discount:

| | Opus 5, low effort, 117 items |
|---|---|
| Live, cached (measured) | $2.68 |
| Batch, if cache reads land | ~$1.34 |
| Batch, if every item re-reads the prefix | **~$10.9** |

So batch is either the single best lever here or a 4x regression, and nothing in
the run so far distinguishes the two.
**Method:** same 117 items through `messages.batches`, 1-hour TTL, one cheap
model first. Read `cache_read_input_tokens` per result.
**Cost:** ~$0.20 for the Haiku probe, ~$1.50 to cover two models.

### 2.2 Multi-item requests (amortise the prefix)
**Question:** what happens if one request judges 10 claims instead of 1?
**Why:** the prefix is re-read per request. Ten claims per request pays that read
once for ten items — potentially a tenth of the bill. It also changes the task
(judging ten together is not judging one), so it needs validating against the
single-item results rather than assumed equivalent.
**Cost:** ~$1. **Bonus:** it is the cheapest route to a larger n.

---

## Priority 3 — the architectures the project was built for

### 3.1 Idle-then-reload (architecture A) — the original question
**Question:** what does an agent actually pay for going idle?
**Why:** this is the scenario that started the project and it has never been run.
Everything measured so far ran against a warm cache. The mock predicted the idle
penalty is small (one extra cache write, ~7.6%), far below the caching effect —
but that is a simulation, not a measurement.
**Method:** four arms over the same items, differing only in what happens in the
gaps:

| Arm | Gap behaviour |
|---|---|
| A | idle past the 5-minute TTL, cache lapses, prefix re-imported |
| B1 | recurring real work keeps it warm |
| B2 | `max_tokens: 0` keep-alive ping just under the TTL |
| B3 | 1-hour TTL instead of a keep-alive |

Run on Sonnet to keep it affordable; the mechanism is not model-specific.
**Cost:** ~$5. **Settles:** learning objective 1, properly.

### 3.2 Orchestrator versus single context (architecture C)
**Question:** where is the crossover at which delegation starts paying?
**Why:** this is learning objective 2, entirely untested. Published measurements
say the split pays only when work exceeds one context window.
**The catch, stated honestly:** scaling to all 923 citations does *not* force
decomposition. 923 claims at ~103 tokens plus a 37k bibliography is roughly 132k
tokens — comfortably inside 1M. To actually exceed the window the corpus needs
**full paper texts instead of abstracts** (≈8k tokens × 393 refs ≈ 3.1M tokens),
which is what makes chunking unavoidable and gives an orchestrator something to
orchestrate.
**Method:** Opus orchestrator plans and delegates per-chunk verification to
Sonnet/Haiku workers; compare against the frontier model alone at low effort on
the same corpus, at several corpus sizes to locate the crossover.
**Cost:** $20–50, the most expensive item here and the most novel result.

---

## Standing method improvements

- **Persist reply text in the run log.** Not doing so is why 1.2 needs a re-run.
- **Report replicates by default.** Single-run comparisons cannot survive
  correction for multiple testing.
- **Raise n cheaply** — two pairs per claim gives 234 items from the existing
  corpus; 2.2 makes a larger n affordable.
- **Keep the error-cost decision map** as the ranking device. Equal-weighted
  accuracy hides which direction a configuration fails in.

## Rough budget

| Priority | Items | Cost |
|---|---|---|
| 1 | replicates + reasoning capture | ~$8 |
| 2 | batch + multi-item | ~$3 |
| 3.1 | idle-then-reload | ~$5 |
| 3.2 | orchestrator crossover | $20–50 |

Priorities 1 and 2 together are about $11 and would settle every claim currently
published. Priority 3 is where the project's original questions live.
