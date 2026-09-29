# Scope, audit, and proposal

Written 2026-09-29, after the project had run for two days and grown well past
its original plan. Three parts, in the order they have to be decided:

1. **Learning objectives** — what this project is for, restated honestly against
   what it actually did.
2. **Methodology** — the one definition of the experiment that everything else
   must agree with.
3. **Proposal** — what to remove, keep, and update in each published artifact.

---

## 0. What we actually did (the scope log)

The project was planned as one comparison — three agent architectures (A: idle
and reload, B: kept warm, C: orchestrator plus cheap workers) on a shared task.
Almost none of that is what got measured. The record:

| Phase | What was added | Cost |
|---|---|---|
| Task selection | Structured extraction → **citation grounding on the author's own review**. The review's `\cite{}` keys became free ground truth. | — |
| Harness | Parser, prompt assembly, pricing table, **cache-faithful mock client**. Everything debugged on free endpoints. | $0 |
| Token counting | `count_tokens` on every prompt; tokenizer gap between Opus 5 and Haiku 4.5 measured at 48% on this text. | $0 |
| Canaries | Four small paid runs to check the forecast before the full one. | $0.85 |
| Full run | Five configurations × 117 items, single call per item. | $8.66 |
| Batch probe | Does the 50% batch discount beat caching? No — 6× worse. | $0.21 |
| Multi-item | Ten claims per request vs one. 70–76% cheaper, identical accuracy. | $0.49 |
| Cost model | Whole-corpus and orchestrator pricing from counted tokens, no calls. | $0 |
| Agent harness | Three Claude Code subagents (Haiku/Sonnet/Opus) on a 40-item slice. | plan usage |
| Cache-TTL A/B | Does a subagent lose its prompt cache over an idle gap? Not at our scale. | plan usage |
| **Two benchmark leaks** | Positional labels, then unresolvable candidate keys. Both found, both fixed. | — |
| **Single-citation rule** | 2026-09-29: claims citing several works dropped. See §2. | — |

Real API spend to date: **$10.21**.

Two deliverables were published from this: a reproducible tutorial
([Token Cost Run Sheet](https://claude.ai/artifact/XmMB18bx7SiKMHJ3rCEDiD)) and a
portfolio piece ([Agent Token Economics](https://claude.ai/artifact/Q1t7xT3JeuUf7MMwmg4MuB),
mirrored as the live Hugo post `tutorial-agent-tokens.md`).

**The honest summary of the drift.** The project set out to measure *how to spend
fewer tokens* and ended up publishing, as its headline, *which model configuration
is most accurate*. Those are different questions. The token findings are the ones
that held up; the accuracy findings are the ones that keep needing re-runs.

---

## 1. Learning objectives

### As originally written

> **LO1.** Save tokens through correct use of memory — avoid costly reloads of
> large context after idle periods.
> **LO2.** Save tokens through task and sub-task breakdown for agents.

### What the work actually supports

Neither original objective survived contact with the measurements, but not
because the work failed — because the measurements answered them faster and more
cheaply than the planned experiments would have, and turned up a bigger lever in
the process.

**LO1 is answered, and the answer is "this was the wrong thing to worry about."**
An idle gap costs exactly one extra cache write. On this prefix that is $0.23 per
pass, about 7.6%, against the 6.8× that *having* a cacheable prefix is worth.
Measured on the agent side too: a 6.5-minute idle gap cost a subagent 424 tokens,
0.6%. Keeping a context warm is a rounding error next to making it cacheable in
the first place.

**LO2 is answered analytically and is not worth running.** All 608 claims plus
the complete 393-entry bibliography come to 184k tokens — one context window.
Decomposition is not forced at this scale, and the cost model says an
orchestrator *loses* ($0.596 against $0.482 for a single agent that scopes its
own context). The saving that does exist comes from **scoping context, not from
delegating**.

So the objectives should be restated to match what this project is now actually
good for:

| | Objective | Status |
|---|---|---|
| **LO1** | Where does the money go in a cached-prefix LLM workload, and which levers move it? | **Met.** Caching 6.8×; grouping 70–76%; 71–88% of every bill is prefix re-reads; batching backfires. |
| **LO2** | When does an idle gap or a context reload actually cost anything? | **Met, negative result.** One cache write; ~7.6%. Both harnesses agree. |
| **LO3** | When does task decomposition pay? | **Modelled, not run.** Not at this scale; crossover is at one context window. |
| **LO4** | How do model tier and reasoning effort trade against accuracy, once errors are priced? | **Measured, then invalidated twice.** Needs the re-run in §4. |
| **LO5** | How do you keep a self-built benchmark honest? | **Met, the hard way.** Two leaks, both found by integrity reporting rather than inspection. |

LO4 and LO5 were not planned. LO5 in particular is the most transferable thing
the project produced and is currently told as an anecdote rather than a finding.

---

## 2. Methodology

One definition. Everything published has to agree with it.

### Unit of analysis

One **pair**: a claim sentence with its citation masked, plus one candidate
citation key. The model answers `supported` or `not_supported`. Grading is exact
string comparison against the author's own key. No rubric, no LLM judge.

### Corpus construction

1. Parse the review's LaTeX. Drop comments, floats, and footnotes before
   sentence splitting — footnote citations are asides, and footnote punctuation
   breaks the splitter.
2. Keep sentences carrying at least one citation and at least 8 prose words.
3. **Mask citations, do not delete them.** `\citet` is grammatical — it is often
   the subject of its sentence. `[CITATION]` keeps the sentence readable and
   marks the slot.
4. **NEW (2026-09-29): keep only sentences with exactly one citation command
   naming exactly one work.**

That last rule is the change made today, and it needs its reasoning on the
record:

> A sentence citing several works has no single right answer to "does this
> reference support the claim?". The reference may support one clause and be
> irrelevant to the rest. A verdict on it is neither clearly right nor clearly
> wrong, so the item contributes noise to accuracy rather than signal.
>
> This was not a hypothetical. The published error analysis found that
> **multi-citation sentences were 44% of positives but 67% of the items that
> three or more configurations failed.** The benchmark was measuring its own
> ambiguity and charging it to the models.
>
> Both halves of the rule matter: a lone `\citep{a,b}` is one citation site but
> two works, and is exactly as ambiguous as two separate sites.

Effect on the Measurement section: **117 claims → 68**, of which 49 were dropped
(25 multi-work single-site, 24 multi-site).

### What the model is shown

The bibliography on display is **every key the section cites — all 93 — including
the keys of sentences the single-citation filter dropped.** The filter governs
what can be *graded*, not what may be *looked up*. Keeping the bibliography whole
has two benefits: the cached prefix stays at its measured 37,276 tokens, so none
of the cost findings move, and negatives are drawn from a richer pool.

### Negative injection

Half the pairs keep the real key; half get a wrong one, at two difficulty tiers:

- **easy** — a key from elsewhere in the bibliography, usually a different topic.
- **hard** — a key cited elsewhere in the *same subsection*: topically adjacent
  and genuinely confusable. This tier is where configurations separate; without
  it every configuration scores about the same.

Two rules learned from the two leaks, both now enforced in code:

- **Negatives are drawn only from the bibliography the model is shown.** Drawing
  from the full 393-entry file produced items whose key could not be looked up —
  and since only negatives were ever drawn that way, "key not found" became a
  perfect predictor of the label. That leaked 20 of 117 items.
- **Labels are assigned by a seeded shuffle, never by position.** `i % 2 == 0` is
  balanced and perfectly predictable. Single-call runs cannot exploit it; an agent
  handed a slice of consecutive items can, and did — 24/24.

### The two harnesses, and what each may be used for

| | Claude API | Claude Code agents |
|---|---|---|
| Measures | cost, exactly, from `usage` fields | workflow, decomposition, retrieval |
| Controls | model, effort, cache TTL, per request | model, prompt, tools |
| Cannot do | tool use, retrieval, a loop | report what it cost — no `usage` |
| Therefore | **anything with a price attached** | **anything with a workflow** |

Agent-arm cost is *derived* by counting the prompts the agent actually sends and
pricing them with the free token counter. It is never billed and never
comparable to an API number: the agent retrieves what it wants, the API arm gets
a fixed prefix. Different information, different task.

### Statistics

Every configuration sees identical items, so comparisons are **paired**:
McNemar's exact test, not overlapping Wilson intervals. Six paired comparisons on
one dataset, so **Holm–Bonferroni** correction. Wilson intervals for single
proportions.

### Standing limits

- **No replicates.** Each configuration has run once. This is the largest hole
  and it is the project's own plan that called for repeats.
- **One task, one domain.** Citation verification rewards scepticism; a task
  rewarding synthesis could reverse the effort finding.
- **Grading measures agreement with the author, not truth.** A claim may be
  supported by a paper the author did not cite. Injected negatives sidestep this:
  a reference the author demonstrably did not cite there is a defensible negative.

---

## 3. The audit

The organising question is not "is this claim interesting?" but **"does this
claim depend on the benchmark labels?"** The labels have now changed three times
(positional → shuffled → resolvable-only → single-citation). Everything that
depends on them is unverified. Everything that does not is untouched.

### Group A — label-independent. Keep as published.

These come from `usage` fields and token counts. No grading enters them. Nothing
that happened to the benchmark touches them.

| Claim | Where |
|---|---|
| Caching is worth 6.8× on this prefix | blog, run sheet |
| 71–88% of every bill is re-reading the bibliography; answers never exceed 11% | blog, run sheet, `token-cost-configurations.svg` |
| Grouping ten claims per request is 70–76% cheaper | blog, run sheet |
| Batch API is 6× *worse* here — 2/10 read the cache, 8/10 wrote it at 2× | blog, run sheet |
| Staggered fan-out converts N−1 writes into reads; 116 reads : 1 write | blog, run sheet, Figure 2 |
| Tokenizer gap: 37,251 vs 25,248 tokens on identical bytes, 48% | blog, run sheet, Table 1 |
| Minimum cacheable prefix: 512 / 1,024 / **4,096** tokens, silent when unmet | run sheet, Figure 1 |
| `input_tokens` is the uncached remainder, not the prompt size | blog, run sheet |
| The uncached baseline is arithmetic, not an experiment | blog, run sheet |
| The mock client caught the bibliography-not-sent bug | blog, run sheet |
| Idle gap = one cache write ≈ 7.6% | run sheet |
| Whole corpus fits one context window (184k), so decomposition is not forced | blog |
| Orchestrator overhead: scoping beats delegating | blog |
| Forecast $9.09 against actual $8.66 | blog, run sheet |

**This group is the project's real contribution and it is intact.** It is also,
notably, the group that answers the original learning objectives.

### Group B — label-dependent. Every number is provisional.

| Claim | Why it is now unverified |
|---|---|
| Five-configuration accuracy table (94.9 / 87.2 / 79.5 / 76.9 / 68.4%) | measured on v1 labels; 56 of 117 items changed label in v2 |
| **"More thinking made Opus worse", 9–0, p = 0.004** | on leak-2-corrected items it is 6–0, p = 0.031 — still there, much weaker |
| **The over-reasoning mechanism** (easy negatives 100% vs 83%) | **collapses.** The gap was mostly 20/20 vs 17/20 on *unresolvable* keys. On resolvable easy negatives it is 9/9 vs 7/9 — nine items, a difference of two. |
| Sonnet effort reversal, raw p = 0.035 | already reported as not surviving correction |
| Per-difficulty breakdown (Haiku sceptical, Sonnet-low credulous) | difficulty tiers were the tiers the leak distorted |
| False accept / false reject rates and the whole error-price plot | derived from the same labels |
| "Opus at default wins nowhere" / two configurations dominated | derived from the same labels |
| "Only 1 of 117 failed by all five"; Jaccard 0.10–0.40; voting doesn't pay | derived from the same labels |
| Multi-item accuracy parity (81.7%, 93.3% both ways) | the *cost* half is Group A; the accuracy half is Group B |
| Three-model agent comparison (0.750 / 0.900 / 0.950) | ran on v2, which still had leak 2 |

### Group C — things that are simply wrong or orphaned

| Item | Problem |
|---|---|
| Run sheet: *"The idle column is the surprise…"* | Table 2 has no idle column. Dangling reference to a table that was edited. |
| Run sheet step 2 output: `single-key: 68 / multi-key: 49` | The script no longer prints this; it now drops the 49. |
| Run sheet step 4 output: `pairs: 117 … 59/58 … easy 29 hard 29` | Now `pairs: 68 … 34/34 … easy 18 hard 16`. |
| Run sheet Table 3: "117 masked claim sentences" | Now 68. |
| Blog, *What the agent actually sees* | **The flagship example is a two-citation sentence** — precisely the kind the new rule excludes. It has to be replaced with a single-citation example. |
| Blog corpus table: 117 / 59 / 58 | Now 68 / 34 / 34. The 93 references and ~37,000-token prefix are unchanged. |
| Blog: "The review has 608 claim sentences" | True, but 402 of them are single-citation — that is the number that now matters. |
| Run sheet masthead: "Reproducible in 50 minutes" | The brief asked for 40. Free steps 1–5 are ~23 min; the paid tail is what pushes it over. |

### Group D — candidates for removal on editorial grounds

Not wrong, just not earning their space.

- **Blog, "Effort may be model-specific — but this one doesn't survive
  correction."** A whole section to report a null result. Fold the two useful
  sentences into the limits section.
- **Blog, the majority-voting paragraph.** Interesting but it is a third result
  in a section already carrying two. Either its own short section after the
  re-run, or cut.
- **Run sheet, the two long `note` boxes inside Table 2.** They duplicate the
  blog. The run sheet's job is reproduction; the argument belongs in the blog.

---

## 4. Proposal

### 4.1 Do this first: the re-run that unblocks everything

Nothing in Group B can be corrected by editing. It needs measurements on the
corrected benchmark. Priced from the measured per-item cost components, grouped
ten per request (which we have shown costs nothing in accuracy):

| Option | Items | Bibliography | Five configs | Resulting CI |
|---|---|---|---|---|
| **A. Section only** | 68 | 93 refs, 37k tokens | **$1.96** | ±11pp — settles nothing |
| **B. Whole review** ← recommended | 402 | 393 refs, 122k tokens | **$14.97** | ±3pp — settles LO4 |
| C. Whole review, k=20 | 402 | 393 refs | **$11.41** | ±3pp, one more variable changed |

Option A is cheap and would produce another set of numbers too weak to
distinguish anything — the same mistake as running 40 items when 117 were
available. Option B is the one that turns LO4 from "measured twice, invalidated
twice" into a result, and it also delivers the full-corpus run the project has
been pointing at since the start.

Two notes on B. Dropping the two dominated configurations would save $8 but would
also remove the arm needed to re-test the headline — keep all five. And the
$14.97 buys one run each; **replication is still unaddressed**, which at 402
items matters less for sampling error but says nothing about run-to-run variance.

### 4.2 Blog post — `tutorial-agent-tokens.md`

| Section | Action |
|---|---|
| Intro, "What this is and isn't" | **Keep.** Already scopes the piece honestly. |
| The task | **Update** — 68 / 34 / 34. Add: "one citation per sentence, and why". |
| What the agent actually sees | **Replace the example.** Use `aref2017measuring`, which is single-citation. Keep the mask-don't-delete lesson — it is still the reason the corpus is readable. |
| The two lines of code | **Keep verbatim.** |
| The three models | **Keep.** |
| The results | **Update** after the re-run. |
| More thinking made Opus worse | **Update and weaken.** 6–0 at p = 0.031 on corrected items. |
| …the over-reasoning mechanism | **Remove.** The data no longer supports it. Replace with one sentence saying a mechanism was proposed and the corrected data does not support it — that is a better story than the original. |
| Effort may be model-specific | **Remove as a section**, fold into limits. |
| Opposite failure modes | **Update.** The shape is the transferable point; keep the shape, re-measure the numbers. |
| Same claims? Mostly not | **Update.** The multi-citation confound paragraph becomes the *motivation* for the single-citation rule — reframe, don't delete. |
| Cost per correct is the wrong ranking | **Update**, keep the argument. |
| Two of five dominated | **Update**, may not survive. |
| Stop asking one claim at a time | **Keep the cost figures, update the accuracy figures.** |
| The Batch API | **Keep verbatim.** |
| The agent that scored 100% | **Keep and extend.** Add leak 2 and the single-citation rule as the second and third instalments. This section is LO5 and is the most reusable thing in the post. |
| Where the money goes | **Keep verbatim.** |
| The baseline I never paid for | **Keep verbatim.** |
| Building it without spending | **Keep verbatim.** |
| Three rules | **Keep verbatim.** |
| What this doesn't tell you | **Update** — add the label-revision history. |
| Why two harnesses | **Keep verbatim.** |
| What the whole corpus would cost | **Update** — 402 single-citation claims. |
| What I'd run next | **Rewrite** around §4.1. |

Images: `token-cost-configurations.svg` **keep**;
`token-cost-quality.svg` and `token-error-cost-regions.svg` **regenerate after
the re-run**.

### 4.3 Run Sheet artifact

Its job is reproduction, and reproduction is currently broken — a reader running
the scripts today gets different console output from what the page shows.

- **Update** every console block to the current script output (steps 2, 4).
- **Add** a step between 4 and 5: verify the benchmark's invariants. One
  citation per claim, every candidate key resolvable, labels not predictable
  from position. This is where LO5 belongs in a run sheet.
- **Remove** the orphaned idle-column paragraph.
- **Update** Table 3 counts and the masthead timing.
- **Extend** the "Balanced is not the same as unpredictable" note to cover all
  three integrity failures, since it is the best-written part of the page.
- **Keep** Figures 1 and 2, Table 1, and the three rules — all Group A.

### 4.4 Repo

- `docs/project-context.md` — restate the objectives per §1; the A/B/C framing is
  no longer what the project is doing.
- `README.md` — corpus counts, and the single-citation rule.
- `docs/next-experiments.md` — R1 is superseded by §4.1; R2 and R3 still stand.
- `scripts/` — a `15_verify_benchmark.py` that asserts the invariants in §2, so
  the next leak fails a check instead of surviving to publication.

### 4.5 What not to do

- Do not re-run at 68 items to save $13. It would produce a fourth set of numbers
  nobody can act on.
- Do not correct Group B numbers by editing them downward. They need
  measurement, not adjustment.
- Do not touch Group A. It is finished, it is correct, and it is what the project
  set out to learn.
