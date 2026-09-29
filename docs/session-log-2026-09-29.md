# Session log — 2026-09-28/29

Findings from the phase-2 and phase-3 work that are not captured elsewhere in
the repo. Raw data in `data/agent_results.json`, `data/results.json`,
`data/error_analysis.json`, `runs/*.jsonl`.

## Spend to date

Real API spend **$10.21** (the `$25.83` in `runs/smoke_mock.jsonl` is the mock
client — simulated, never billed). Agent runs consumed Claude Code plan usage,
not API credit.

| | |
|---|---|
| Full 5-configuration run | $8.66 |
| Canaries (4) | $0.85 |
| Multi-item grouping (2) | $0.49 |
| Batch probe | $0.21 |

## Agent-harness model comparison

Three Claude Code subagents, identical prompt, 40-item balanced slice
(20 supported / 20 not; 20 positive, 10 easy negative, 10 hard negative) drawn
stratified from the shuffled-label benchmark. Each agent got its own directory
outside the repo containing only `task.json` and `bibliography.jsonl`.

| Model | All 40 | Resolvable 32 | Positives | Hard neg | Said supported |
|---|---|---|---|---|---|
| Haiku 4.5 | 0.750 | 0.688 | 0.60 | 0.82 | 14/40 |
| Sonnet 5 | 0.900 | 0.906 | 0.95 | 0.82 | 22/40 |
| Opus 5 | 0.950 | 0.938 | 0.95 | 0.91 | 20/40 |

McNemar: opus beats haiku (9–1, p = 0.022). Sonnet vs opus and haiku vs sonnet
are **not distinguishable** at this sample size. Agreement: sonnet/opus 36/40,
the others 30/40.

**The open question this raises.** In the API run on resolvable items Sonnet
scored 0.784, third place and barely above Haiku. In the agent harness it
reaches 0.906, close to Opus. Haiku and Opus barely moved between harnesses.
If real, the tier gap narrows when a model retrieves for itself rather than
being handed a fixed prefix — which matters because Sonnet is 2.5x cheaper than
Opus. At n=32 the intervals are ~±15pp, so this is a hypothesis, not a result.
It needs the full 117 on the corrected benchmark.

**Not comparable to the API numbers.** A subagent has tools and retrieves what
it wants; the API arm got a fixed cached prefix and one item per request.
Different information, different task.

## Subagent prompt-cache TTL: tested, no effect at our scale

Claude Code gives the **main conversation a one-hour** prompt cache on a
subscription but **subagents five minutes**, per the prompt-caching docs. The
controls are `subagentPromptCacheTtl` / `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`,
or per-agent `experimental.cacheTtl` frontmatter (v2.1.242+; the frontmatter
value is ignored while the subscription draws on usage credits).

A/B on identical 10-item tasks, same model, differing only in an idle gap:

| | Tokens | Tool calls | Duration | Correct |
|---|---|---|---|---|
| No gap (2s) | 65,815 | 7 | 86s | 7/10 |
| Gap (390s) | 66,239 | 8 | 470s | 8/10 |

**A 6.5-minute idle gap cost 424 tokens, 0.6%.** No context rewrite. The
bibliography alone is ~25k tokens on Haiku, so a real lapse would have been
visible in the tens of thousands. Both agents independently reported not needing
to re-read the bibliography after the gap.

**Decision: do not set the TTL.** Two caveats. The Reddit report that prompted
this describes agents holding hundreds of thousands of tokens and sitting inside
long *blocking* tool calls; ours held 66k and its wait was backgrounded, and a
lapse costs in proportion to context size. And `subagent_tokens` has an unknown
definition — a write-rate rewrite might not surface in it at all. Re-test if we
ever run agents with much larger contexts.

## Environment and harness gotchas

- **`$HOME` is `/o/`** in this Git Bash, not the Windows profile. User settings
  live at `C:\Users\giacomov\.claude\settings.json`; checking `$HOME/.claude`
  wrongly reports no settings file. That file has real content
  (`permissions.allow`, `effortLevel`, `model`) — do not create it blind.
- **`.claude/agents/` needs a session restart.** Custom `subagent_type` values
  do not resolve in a running session; dispatch fails with "Agent type not
  found". Plan agent experiments accordingly.
- **No `claude` CLI on disk** (VSCode extension runs it internally), so the
  documented `claude -p --output-format json` cache check is unavailable here.
- **Subagents expose no `usage` fields.** The only observable is
  `subagent_tokens` in the completion notification, meaning undefined. Agent-arm
  cost must be derived by counting prompts, never billed.
- Agents may write JSON with a **UTF-8 BOM** — read with `utf-8-sig`.

## Two benchmark leaks, both found and fixed

1. **Positional labels.** `make_positive = (i % 2 == 0)` made every label
   predictable from item index; all 117 alternated. An agent handed a 24-item
   slice scored 24/24. Fixed with a seeded shuffle.
2. **Unresolvable candidate keys.** Negatives were drawn from all 393 references
   while the prompt carried only the 93 cited in the section, so 20 of 117 items
   had a key the model could not look up — and only negatives were ever drawn
   that way. Fixed by drawing negatives from the shown bibliography only.

Both leaks were surfaced by integrity reporting, not by inspection: asking each
agent to list the files it read, and to flag keys it could not resolve.

Versions kept for provenance: `data/pairs_v1_alternating.jsonl` (published run),
`data/pairs_v2_shuffled.jsonl` (leak 1 fixed), `data/pairs.jsonl` (both fixed).
