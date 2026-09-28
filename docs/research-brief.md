# Research brief: what the current Anthropic docs say about our five axes

Compiled 2026-09-28 from the live platform docs (pricing, batch processing) and the
bundled `claude-api` reference (caching, cost optimization, agent design). Every number
below is sourced, not recalled. Re-verify pricing before publishing the tutorial.

This brief exists to be read **before** we build the harness, because four of its findings
change what the experiment should measure.

---

## 1. Verified pricing (USD per million tokens, Claude API first-party)

| Model | Input | Output | 5m cache write | 1h cache write | Cache read | Batch in/out | Context |
|---|---|---|---|---|---|---|---|
| Claude Opus 5 (`claude-opus-5`) | $5 | $25 | $6.25 | $10 | $0.50 | $2.50 / $12.50 | 1M |
| Claude Sonnet 5 (`claude-sonnet-5`) | $2 | $10 | $2.50 | $4 | $0.20 | $1 / $5 | 1M |
| Claude Haiku 4.5 (`claude-haiku-4-5`) | $1 | $5 | $1.25 | $2 | $0.10 | $0.50 / $2.50 | 200K |

Source: platform.claude.com/docs/en/about-claude/pricing.

Two things to note before designing architecture C:

- **The tier ratio is 5 : 2 : 1 on both input and output.** So the absolute ceiling on
  worker-model savings is 5x on whatever share of work you delegate — before you pay for
  the plan, the handoff, and the merge.
- **Sonnet 5 is $2/$10, not $3/$15.** The increase scheduled for 2026-09-01 was cancelled
  and the introductory price became standard. That makes the Sonnet-to-Haiku gap only 2x,
  while Haiku costs you a 200K context ceiling and a much worse cache minimum (see §2).
  Haiku has to earn that 2x; don't assume it does.

### The tokenizer trap — this one affects our headline metric

Claude 4.7 and later models use a newer tokenizer that produces **roughly 30% more tokens
for the same text**. Opus 5 and Sonnet 5 are on it; Haiku 4.5 is on the previous one.

Consequence: **token counts are not comparable across models.** A tutorial reporting
"architecture C used 40% fewer tokens" would be measuring the tokenizer, not the
architecture. Every headline number has to be **dollars per completed task**. Keep token
counts in the logs as diagnostics, never as the comparison.

---

## 2. Prompt caching — the mechanism behind both learning objectives

**The invariant:** caching is a prefix match. Any byte change anywhere in the prefix
invalidates everything after it. Render order is `tools` -> `system` -> `messages`.

**Economics:** a write costs 1.25x base input (5-minute TTL) or 2x (1-hour TTL); a read
costs 0.1x. Break-even is two requests on the 5-minute TTL (1.25x + 0.1x = 1.35x vs 2x
uncached), three on the 1-hour TTL (2x + 0.2x = 2.2x vs 3x).

**TTL is chosen by the start-to-start gap between requests sharing the prefix:**

| Gap | TTL |
|---|---|
| Under 5 min | 5-minute — every request refreshes it, strictly cheaper |
| 5-60 min | 1-hour — the only window where the 2x write pays off |
| Over 1 hour | Neither. Re-warm on a schedule, or accept the cold miss |

A cache read refreshes the entry's timer **at no extra cost**, and lifetime is measured
from the **start** of the request — so a 4-minute generation leaves only about a minute of
a 5-minute entry.

### This splits architecture B in two, and B2 is the interesting one

Our plan for B was "recurring tasks keep the agent warm." The docs describe something much
cheaper: a **`max_tokens: 0` pre-warm / keep-alive**. The API runs prefill, writes the
cache, and returns immediately with `content: []`, `stop_reason: "max_tokens"`, and a
populated `usage` — **zero output tokens billed**, only the cache charge.

So B should be two arms:

- **B1** — keep warm with real recurring work (what the project doc assumed).
- **B2** — keep warm with a bare `max_tokens: 0` ping fired just under the TTL.

B2 should dominate B1 by a wide margin, and that gap is a clean, quotable result: *you do
not need to invent busywork to keep a cache alive.* Caveats: the pre-warm's `cache_control`
must sit on the last block **shared with the real request** (not on the placeholder user
message, and not via top-level automatic caching, which would key the entry to the
placeholder); and the ping must send the **same `thinking` and `effort` settings** as real
traffic, or it writes an entry real traffic never reads. `max_tokens: 0` is rejected with
`stream: true`, structured outputs, forced `tool_choice`, and inside a Batch request.

### The cacheable minimum is non-monotonic, and it bites architecture C

| Model | Minimum cacheable prefix |
|---|---|
| Opus 5 | 512 tokens |
| Sonnet 5 | 1024 tokens |
| Haiku 4.5 | **4096 tokens** |

Shorter prefixes silently don't cache — no error, just `cache_creation_input_tokens: 0`.
If we decompose to one paper per Haiku worker and each worker prompt is a couple of
thousand tokens, **none of the worker prompts cache at all**, and nothing tells us so.
Check `cache_creation_input_tokens` on the first worker call before trusting any
architecture-C number.

### Caches are model-scoped

There is no escape hatch for a model switch. The orchestrator and the workers can never
share a cache entry. That cost is structural to architecture C, not a tuning problem.

### Parallel fan-out defeats itself

A cache entry becomes readable only after the first response **begins streaming**. N
parallel requests with identical prefixes therefore all pay full price — none can read what
the others are still writing. The documented fix: **send 1 request, await the first
streamed token, then fire the remaining N-1.**

This is probably the most counterintuitive thing we can demonstrate, it is cheap to
measure, and it is a direct A/B inside architecture C (naive fan-out vs staggered fan-out).

### Verification

`usage.cache_read_input_tokens` is ground truth. Note that **`input_tokens` is the uncached
remainder only** — total prompt size is `input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`. A healthy steady-state loop: reads grow turn over turn, writes
stay small (roughly the last turn's delta), `input_tokens` is just the tail.

---

## 3. Context editing vs compaction vs memory — three different things

The project doc treats these as one axis. They are not:

| Feature | What it does | How |
|---|---|---|
| **Context editing** | *Clears* stale tool results / thinking blocks | beta `context-management-2025-06-27`; `context_management.edits` with `clear_tool_uses_20250919` or `clear_thinking_20251015` |
| **Compaction** | *Summarizes* earlier context server-side | beta `compact-2026-01-12`; default trigger about 150K tokens |
| **Memory tool** | Cross-session persistence via a file directory | `{"type": "memory_20250818", "name": "memory"}`; you implement the backend |

Compaction pitfall: you must append the **full `response.content`** back on every turn, not
just the text. The compaction blocks are what the API uses to replace compacted history;
extracting only the text silently loses the state.

**Both editing and compaction invalidate the cache from the edit point onward.** That makes
them a genuine trade against caching, not a free add-on, and it deserves its own row rather
than being folded into "memory."

Note also that objective 1 in the project doc ("correct use of memory") is really two
mechanisms: cache TTL + keep-alive (within a session, §2) and the memory tool (across
sessions). The tutorial should name them separately.

---

## 4. Batch API — and an uncomfortable implication

50% off **every token in the request, including cache reads and writes**; the discounts
stack. Limits: 100,000 requests or 256 MB per batch, whichever comes first; most batches
finish inside an hour, unfinished ones **expire at 24 hours** (unbilled); results are
retrievable for 29 days. Results arrive in **any order** — key by `custom_id`, never by
position. Batch requests are single-shot: no mid-batch tool loop. Cache hits inside a
concurrent batch are best-effort, and the docs recommend the **1-hour TTL** for batches
with shared context, since a batch can easily exceed 5 minutes. `max_tokens: 0` is rejected
inside a batch.

**The implication we should not dodge:** our task — 30 papers in, one JSON record each out,
nobody waiting — is the textbook batch workload. A flat batch of N single-shot extraction
calls at 50% off, over a shared cached prefix, may well beat **all three** agent
architectures on dollars per completed task.

That is worth confronting rather than hiding. It makes a sharper tutorial than a staged
win: *here is the decomposable task, here are three agent architectures, and here is the
boring non-agentic baseline that beats two of them.* I'd add it as architecture **Z — batch
baseline** and report it first.

---

## 5. Effort and thinking budgets

- `output_config: {effort: "low"|"medium"|"high"|"xhigh"|"max"}` — inside `output_config`,
  not top-level. Default is `high` on Opus 5.
- Supported on Opus 5 and Sonnet 5 (all five levels). **Not supported on Haiku 4.5** — it
  errors. Haiku 4.5 still uses `thinking: {type: "enabled", budget_tokens: N}`.
- `budget_tokens` is **removed on Opus 5 and Sonnet 5** — it returns a 400. Use adaptive
  thinking plus effort.
- Changing effort mid-session **invalidates the messages cache**, so each effort level must
  be swept in its own session or the comparison is distorted.

**Measured expectations, from Anthropic's own runs:** on research and knowledge work the
effort curve is nearly flat — `low` gave up 1-3 points for a third to a half off cost per
task, and `medium` matched the default's accuracy at 70-85% of its cost. Structured
extraction is knowledge work, so we should expect a flat curve and a cheap, strong result.
Long-horizon coding is where the curve actually bites (about 2 points at `medium` for half
the cost, about 8 points at `low` for a quarter) — worth stating in the tutorial so readers
don't over-generalize from our task.

### The re-run-failures pattern deserves to be its own architecture

Run everything at `low`, then re-run only the failures at the default effort. Anthropic's
coding runs: about 93% pass at about $0.70/task, versus 91.7% at $1.39 running everything
at the default — the same pass rate for half the cost, counting the wasted cheap attempts.

This fits our task **better than it fits coding**, because exact-match grading against a
gold key gives us a free, perfect failure signal. I'd make it architecture **D**. It may be
the best cost-per-completed-task result in the whole study, and it is a genuinely reusable
lesson.

---

## 6. Subagent isolation — and the measured verdict on orchestrators

Mechanically: subagents start with a fresh context, so only what you pass crosses the
boundary; pass file paths, decisions and constraints explicitly. A fork that rebuilds
`system` / `tools` / `model` with *any* difference misses the parent's cache entirely — to
share it, copy the parent's fields verbatim and append fork-specific content at the end.

**The verdict we need to plan around.** Anthropic's measured result on the orchestrator
pattern (frontier model plans, cheaper workers execute):

> It buys something only when there is bulk to hand off — many independent pieces, ideally
> **too many for one context window**. On work larger than any context window it cost 55%
> less than the frontier model solo at every effort setting (3-7 points below its best
> score). When the work is one dependent chain, **or fits in a single context**, the
> orchestrator pays for a plan, a handoff and a merge that a single model gets for free —
> and in every such case measured, the coordinator's model alone at lower effort came out
> ahead.

This is the live constraint for the chosen task. The Measurement section — 122 claims over a
393-entry bibliography — fits comfortably inside Opus 5's 1M-token context. **At that scale
architecture C is designed to lose,** and we should expect it to.

The full review (923 citations, and more if we add full texts rather than abstracts) is
what gives C a fair test. Since we are starting on the Measurement section and scaling
afterwards, the natural output is the **crossover**: the corpus size at which the
orchestrator stops paying for a plan, a handoff and a merge, and starts saving. Nothing in
the docs gives that number for a task like ours — it is the most interesting thing this
project could produce.

What to avoid is quietly choosing a scale that flatters C. Report the section-scale loss
and the full-scale result side by side.

---

## 7. Measurement traps for the harness

1. **Report dollars per completed task, not tokens** (§1, tokenizer).
2. **`input_tokens` is the uncached remainder only** — sum all three usage fields.
3. **Cache state leaks between runs.** Complete every sample at one setting before starting
   the next, in a stable order, or the comparison measures cache warmth.
4. **Repeat runs.** A difference of a task or two of accuracy, or cents of mean cost, is
   noise on a single run.
5. **Price the tail, not the median.** On one 20-problem research run, two problems carried
   43% of the spend. Log per-paper cost, not just the total.
6. **Judge cost per *completed* task.** A cheaper request that needs more turns or a retry
   isn't cheaper.
7. **Assert caching in a test.** A second identical request must show
   `cache_read_input_tokens > 0`. Caching regressions are silent — requests keep
   succeeding, the bill is just higher.
8. Set a **workspace spend limit** before the first run. Batches can slightly overshoot it.

---

## 8. Proposed revision to the experiment matrix

| Arm | Description | Why |
|---|---|---|
| **Z** | Batch baseline: N single-shot calls, shared cached prefix, 1h TTL | The honest non-agentic floor (§4) |
| **A** | Single Opus agent, idles past the TTL, reloads context | Baseline waste, as planned |
| **B1** | Opus kept warm by real recurring work | As planned |
| **B2** | Opus kept warm by a `max_tokens: 0` ping | Should dominate B1 (§2) |
| **C-naive** | Opus orchestrator + Sonnet/Haiku workers, parallel fan-out | Expect cache misses (§2) |
| **C-staggered** | Same, but send 1, await first token, then fan out | Isolates the fan-out effect |
| **D** | All at `low` effort, re-run failures at default | Likely best $/task (§5) |

Orthogonal flags per arm: caching on/off, batch on/off, effort level, context editing
on/off.

---

## 9. Testing without paying

There is no free Anthropic API sandbox — no emulator, no free tier that returns real
completions. But most of this build can be validated for nothing, in three layers:

**Layer 1 — mock client (free, no API key).** A fake client returning canned responses and
synthetic `usage` numbers. Exercises prompt assembly, arm orchestration, the run-log schema,
the grader, cost accounting and the comparison table. That is where the bugs are; none of it
needs a real model.

**Layer 2 — `count_tokens` (free, needs a key, does not bill).** The docs state plainly that
token counting is **free to use**, at 5,000 RPM on the Start tier, on a **rate-limit pool
separate from message creation**. It counts under the **real tokenizer of the model you
pass**, which is exactly the problem §1 warns about. Since our headline metric is input
tokens x price, this gives us the full predicted-cost table per arm before spending a cent.
Output tokens are the only component that needs a real run. Note the endpoint ignores
caching — `cache_control` is accepted but no caching occurs — so it prices the *uncached*
prefix; apply the cache multipliers yourself.

**Layer 3 — cheap real pilot.** Haiku 4.5 + Batch (50% off) on 5-10 claims, to calibrate
output length and confirm cache behaviour against `usage`.

Order-of-magnitude estimates from measured byte counts (crude — a char/token ratio, which is
precisely what Layer 2 replaces):

| Arm | Estimated cost per full 122-claim pass |
|---|---|
| Opus 5, **uncached** (arm A, deliberately cold) | ~$78 |
| Opus 5, cached | ~$9 |
| Haiku 4.5, cached | ~$2 |
| Haiku 4.5, cached + batch | ~$1 |

The shape is the point: caching turning ~$78 into ~$9 is the tutorial's headline, and arm A
is the only genuinely expensive arm — subsample it rather than running it in full.

---

### First mock result: the idle penalty is much smaller than the project assumed

The Phase 0 harness, run on all 117 pairs against the mock (no API calls), gives:

| Arm | Cost | vs cached baseline |
|---|---|---|
| A-nocache (Opus, caching off) | $18.04 | 6.8x |
| A-idle (Opus, cached, one cold reload) | $2.65 | 1.0x |
| B2-keepalive (Opus, cached, pings bridge the gap) | $2.60 | 0.98x |
| Z-batch (Opus, 1h TTL, batch) | $1.30 | 0.49x |
| C-naive (Haiku, parallel fan-out) | $0.74 | — |
| C-staggered (Haiku, send-one-then-fan-out) | $0.50 | -33% vs naive |

Two things to carry into the real runs:

1. **Caching is the whole story; idling is a rounding error.** Learning objective 1
   assumed that going idle and reloading context is a major cost. With caching on it
   is not: one idle gap costs one extra cache *write* (~$0.19 here), while the
   keep-alive pings that avoid it cost reads (~$0.10). B2 beats A-idle by about 2%.
   The 6.8x figure comes from caching vs no caching, not from idling. The honest
   framing for the tutorial is: **keeping the cache warm matters far less than having
   a cacheable prefix at all.** B2 only becomes decisive when gaps are frequent
   relative to the work done between them.
2. **Fan-out ordering is worth a third of the bill**, for free, with no quality cost
   (§2). That is the cheapest real win the study has found so far.

These are mock numbers with estimated token counts; they validate the accounting and
the direction, not the magnitudes. Layer 2 (`count_tokens`) replaces the estimates.

### Layer 2: exact counts (measured 2026-09-28, free, nothing billed)

Cached prefix = the 93-reference bibliography with abstracts, 119,503 chars.

| Model | Prefix tokens | Mean tail | vs Opus 5 |
|---|---:|---:|---:|
| Claude Opus 5 | 37,251 | 535 | 1.00x |
| Claude Sonnet 5 | 37,251 | 535 | 1.00x |
| Claude Haiku 4.5 | 25,248 | 366 | **0.68x** |

**The tokenizer gap is larger on our content than the docs' rule of thumb.** The docs
say Claude 4.7+ produces "approximately 30% more tokens for the same text"; on this
bibliography it is **48% more** (37,251 vs 25,248). Bibliographic text - proper names,
initials, DOIs, LaTeX residue - tokenizes worse than prose. This is the concrete case
for §1's rule: never compare token counts across models, and never reuse a count taken
on one model to budget another.

The char/4 estimate used in the mock gave 29,875 tokens against a true 37,251 - **25%
low**. Directionally fine, materially wrong for budgeting.

Predicted cost for one full pass over 117 items (USD), 120 output tokens assumed:

| Model | Uncached (derived) | Cached 5m | + 1 idle reload | Cached 1h + batch | Cache ratio |
|---|---:|---:|---:|---:|---:|
| Opus 5 | 22.46 | 3.06 | 3.29 | 1.60 | 7.3x |
| Sonnet 5 | 8.98 | 1.22 | 1.32 | 0.64 | 7.3x |
| Haiku 4.5 | 3.07 | 0.44 | 0.47 | 7.0x | |

Confirmations and corrections:

- **Caching is worth 7.3x**, close to the mock's 6.8x. The headline holds.
- **The idle penalty is 7.6%**, not the mock's 2% and nowhere near the project's
  original premise. One idle gap costs one extra cache write: $0.23 on Opus. Real,
  but an order of magnitude below the caching effect.
- **Haiku is 7x cheaper than Opus, not 5x.** The list-price ratio is 5:1, but Haiku's
  older tokenizer reads the same bibliography in 32% fewer tokens, compounding to 7x.
  A per-token price list under-predicts the true gap here.

---

## 9b. RESULTS — full run, 2026-09-28

585 live requests, **$8.66 actual against $9.09 forecast (0.95x)**. Raw data in
`data/results.json`, per-request log in `runs/full_run.jsonl`.

| Configuration | Accuracy | 95% CI | Cost | $/correct |
|---|---|---|---|---|
| Opus 5, low effort | **94.9%** | 89.3–97.6 | $2.68 | $0.0241 |
| Opus 5, default | 87.2% | 79.9–92.1 | $3.29 | $0.0323 |
| Sonnet 5, default | 79.5% | 71.3–85.8 | $1.23 | $0.0132 |
| Haiku 4.5 | 76.9% | 68.5–83.6 | $0.38 | **$0.0042** |
| Sonnet 5, low effort | 68.4% | 59.5–76.1 | $1.09 | $0.0136 |

**Headline: more thinking made Opus worse.** Low effort beat default 94.9% to 87.2%
at 19% less cost. Paired McNemar: low right on 9 items where default was wrong, 0 the
other way, p = 0.004. On *easy* negatives low scored 100% against default's 83% — given
more room to reason, the model builds a bridge to an unrelated paper and believes it.

**Effort is model-specific.** Sonnet went the opposite way (79.5% default vs 68.4% low,
p = 0.035), collapsing to 38% on hard negatives. There is no portable effort default.

**Opposite failure modes hide behind similar accuracy.** Haiku is sceptical (64% on
positives, 86–93% on negatives); Sonnet-low is credulous (90% positives, 38–55%
negatives). Aggregate accuracy conceals which way a configuration fails.

**Sonnet over Haiku bought nothing detectable** — 79.5% vs 76.9%, p = 0.74, at 3.2x the
price.

**Two of five configurations are strictly dominated**: Sonnet-low (beaten by Haiku on
both axes) and Opus-default (beaten by Opus-low on both).

**Cost structure (answers §2's question empirically):** 71–88% of every configuration's
bill is re-reading the cached bibliography; writing the answer is never above 11%.
Thinking is 20.8% of Opus-default and 2.7% of Opus-low.

**Forecasting worked.** Four canaries ($0.85) all landed within 10% of prediction. The
one assumption that was badly wrong — 120 output tokens per reply against Opus's actual
378 — is precisely the quantity a free endpoint cannot supply, which is the argument for
canarying before a full pass.

---

## 10. Open questions

1. **Budget ceiling** for the whole study, which decides how far we scale §6.
2. **Free credits / academic discount.** New accounts get some credits and the pricing page
   mentions academic and research discounts — worth asking before the first paid run.
3. **Whether to include Claude Fable 5.1** as a fourth tier. Its cache reads are 0.025x
   ($0.25/MTok), which changes the caching arithmetic enough to be interesting, but it adds
   a fourth model to every run.
4. **Evidence depth.** Abstracts only, or full texts for the papers we have? Full texts make
   the task harder and much larger — which is one way to push past the context window for §6.

**Resolved:** implementation language (Python, env `agentflow` ready); corpus and licensing
(the author's own review); gold-key authoring (free — the `\cite{}` keys are the key).
