# Project context: agentic AI and token optimization tutorial

## Purpose
A personal test-bed to learn how to use gen AI well and to showcase that understanding. The end product is a tutorial plus reusable assets (Claude instructions, subagent definitions, skills).

## Learning objective
How to save tokens. Two methods to learn and demonstrate:
1. Correct use of memory (avoid costly reloads of large context after idle periods).
2. Task and sub-task breakdown for agents.

## Experiment: same task, three architectures
**Task chosen 2026-09-28: citation grounding on the author's own structural-balance review.**

Source: `../../git/SBreview` (`quantyfyingSB_v2.tex`, `references.bib`). The review is the
ground truth: the `\cite{}` keys the author wrote define correct attribution.

Two graded tasks over the same corpus:
- **Primary — verification with injected negatives.** Agent sees a claim sentence plus one
  candidate reference and answers supported / not-supported. A controlled fraction of pairs
  is corrupted with a wrong-but-plausible key. Exact binary grading, balanced classes,
  tunable difficulty (random wrong key = easy; same-subsection key = hard tail).
- **Secondary — attribution.** Citation stripped; agent names the supporting reference(s)
  from the bibliography. Graded as retrieval (exact set match / recall@k). Noisier, since a
  correct-but-uncited answer scores as a miss — reported as a second lens on whether the
  architecture ranking is stable across grading schemes.

Corpus figures (measured 2026-09-28): Measurement section = lines 839-1715, 877 lines,
85 KB, **122 cited sentences**, 95 unique keys. Bibliography = 393 entries, 511 KB, 261
with abstracts. Whole review = 923 citation commands.

**Why this shape matters:** the bibliography is a large, byte-stable shared prefix and each
claim is a tiny query against it. That is the ideal pattern for demonstrating caching and
keep-alive, and the opposite of a task where every sub-task drags in its own large unique
document, which can never cache.

Scope: build and debug on the Measurement section (122 claims), then scale to the full 923.
Abstracts for entries missing them are fetched once via Crossref so evidence quality is
uniform across claims.

See `research-brief.md` for the revised arm list and for why architecture C needs the
full-review scale to get a fair test.

- **A. Single Opus agent that idles.** Starts a task, sits idle, and the heavy context must be re-imported afterwards. Baseline for token waste.
- **B. All-Opus with recurring tasks.** Recurring activity keeps the agent from idling so the big context is not reloaded. Expected to be costly because of Opus pricing.
- **C. Opus orchestrator plus cheaper workers.** One Opus agent supervises, orchestrates, and holds the big memory. Sonnet 5 and Haiku 4.5 agents do background sub-tasks. Requires breaking the task into small, simple pieces.

## Extra axes worth testing (added in discussion)
These are orthogonal to A/B/C, so each can be a row in the comparison table. Check current docs before relying on details.
- Prompt caching: avoids reprocessing a repeated prefix.
- Context editing and compaction: clear stale tool results/thinking, or summarize history.
- Batch API: flat discount for work that does not need an immediate answer.
- Effort / thinking budget: tune reasoning depth per sub-task, separately from model choice.
- Subagent context isolation: subagents start with a fresh context, so only the prompt you pass crosses the boundary. Pass file paths, decisions, and constraints explicitly.

## Measurement
Log for every run: model, input/output/cached tokens, cost, wall time, number of turns, and output quality against a fixed check. Keep the task, prompts, and grading criteria identical across architectures. Repeat runs, since outputs are stochastic.

## Deliverables (reusable after the tutorial)
- `CLAUDE.md` with general instructions for token-aware work.
- `.claude/agents/*.md`: reusable subagent definitions (model, tools, prompt).
- Skills (SKILL.md bundles) for repeatable procedures, such as task decomposition.
- The tutorial itself.

## Decided
- Task: structured extraction from a paper corpus (2026-09-28).
- Quality measure: exact match against a fixed gold key.
- Tutorial audience: engineers already building agents (2026-09-28).

## Open decisions
- Corpus source and licensing; corpus size (30 vs a few hundred) — see `research-brief.md` §6.
- Gold-key authoring effort; it is the project's critical path.
- Budget ceiling for the whole study.
- Implementation language (Python env is ready — see CLAUDE.md; Node v24 also available).

## Later project (not part of this folder yet)
Project 2 tests whether expert-level prompting changes LLM output quality, using three simulated-expertise agents plus the author as a human-expert condition, with prompt and reasoning logging. It may reuse the same orchestrator/subagent harness.
