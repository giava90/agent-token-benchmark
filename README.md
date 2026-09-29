# Citation-grounding benchmark for agentic token optimization

A test-bed measuring what different agent architectures cost to do the same job.
The job: check citations in a structural-balance review against its own
bibliography. The review's `\cite{}` keys are the ground truth, so grading is
exact and free.

See `docs/project-context.md` for the experiment design and `docs/research-brief.md`
for the sourced findings the design rests on.

## Pipeline

```
scripts/01_build_corpus.py     tex + bib  -> claims.jsonl, refs_*.jsonl
scripts/02_fetch_abstracts.py  fill missing abstracts (OpenAlex/Crossref/arXiv)
scripts/03_build_pairs.py      claims     -> pairs.jsonl (positives + negatives)
scripts/04_smoke_mock.py       full harness against the mock client
scripts/15_verify_benchmark.py assert the benchmark's integrity invariants
```

Run them in order with the project interpreter (see `CLAUDE.md`):

```
<python.exe> scripts/01_build_corpus.py
```

Steps 1-4 make **no API calls and cost nothing**.

## Current corpus (Measurement section)

| | |
|---|---|
| Claims | 68 — every one citing exactly one work |
| Dropped as multi-citation | 49 of 117 |
| Unique cited keys shown | 93, all resolving in `references.bib` |
| Abstract coverage | 84/93 after fetching |
| Verification pairs | 68 — 34 supported, 34 not (18 easy, 16 hard negatives) |
| Cached prefix | ~120k chars, 37,276 tokens on Opus 5 |

**Only single-citation sentences are graded.** A sentence citing several works
has no single right answer to "does this reference support the claim?" — the
reference may support one clause and be irrelevant to the rest. The bibliography
on display is still all 93 keys the section cites, including those of the
dropped sentences: the filter governs what can be graded, not what may be looked
up, so the cached prefix is unchanged. Pass `--allow-multi-cite` to keep them.

Whole review: 608 cited sentences, of which **402 are single-citation**.

The 9 references still without an abstract are pre-digital classics
(Erdős–Rényi 1959, Gilbert 1959, Chung 2002 and similar) with no
machine-readable abstract at any source. They are flagged, not faked.

## Modules

| Module | Role |
|---|---|
| `bib.py` | BibTeX parsing (brace-matched — a regex split silently drops entry 1) |
| `latex.py` | comments, float/math environments, sentence splitting, citation masking |
| `corpus.py` | claim extraction and the reference index |
| `abstracts.py` | OpenAlex → Crossref → arXiv abstract lookup |
| `negatives.py` | injected negatives at two difficulty levels |
| `prompts.py` | prompt assembly — stable prefix vs per-claim tail |
| `pricing.py` | verified price table and cost accounting |
| `mockclient.py` | fake client that reproduces the real cache rules |
| `grading.py` | verification and attribution graders |
| `arms.py` | the experiment arms |
| `runlog.py` | one JSONL row per request |

## What the mock does and does not do

It reproduces the cache rules that decide the study's numbers: prefix matching,
per-model minimum cacheable prefix (silent when too short), TTL measured from
request start, free refresh on read, model-scoped entries, and the rule that a
cache entry is readable only once the writing request has begun responding.

It does **not** produce real answers. Its accuracy is a seeded coin flip, equal
across arms by construction — so **accuracy columns in mock runs are not a
quality signal**, only a check that grading works end to end.

## Metric

Report **USD per completed task**. Token counts are diagnostics only: Claude 4.7+
models use a newer tokenizer that yields roughly 30% more tokens for the same
text, so token counts are not comparable across models.

`input_tokens` from the API is the uncached remainder only. Total prompt size is
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
