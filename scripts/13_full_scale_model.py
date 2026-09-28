"""Full-corpus cost model: does the whole review need decomposition at all?

Free. Extends the measured token counts to all 608 claims and prices three
architectures, with orchestrator overhead counted from real prompts rather than
assumed:

  single-context : one cached bibliography, N requests against it
  grouped        : k claims per request (measured: same accuracy, ~70% cheaper)
  orchestrator   : a planner that chunks the corpus and delegates to workers,
                   paying for a plan, a handoff per chunk and a merge

The question the project was built to answer is where the third one starts to
pay. The answer here is uncomfortable and worth stating plainly.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, counting, pricing, prompts  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
CLAIMS_FULL = 608          # measured across the whole review
CONTEXT = {"claude-opus-5": 1_000_000, "claude-sonnet-5": 1_000_000,
           "claude-haiku-4-5": 200_000}

PLAN_PROMPT = """You are coordinating a citation-verification pass over a large review.
The corpus is split into chunks. For each chunk, dispatch a worker with the
chunk's claims and the slice of the bibliography those claims cite. Collect the
verdicts, resolve disagreements, and produce one consolidated worklist of
citations that look unsupported, ranked by confidence."""


def main() -> int:
    counts = json.load(open(os.path.join(DATA, "token_counts.json"), encoding="utf-8"))["counts"]
    section_refs = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    all_refs = corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))

    # Count the real orchestrator overhead prompts rather than guessing them.
    ctr = counting.Counter("claude-opus-5")
    plan_tok = ctr.count(PLAN_PROMPT)
    full_bib_tok = ctr.count(prompts.bibliography_block(all_refs))
    print(f"measured: plan prompt {plan_tok} tokens; "
          f"full 393-entry bibliography {full_bib_tok:,} tokens\n")

    print("=" * 74)
    print("DOES THE FULL REVIEW EVEN NEED DECOMPOSITION?")
    print("=" * 74)
    tail = counts["claude-opus-5"]["mean_tail_tokens"]
    single_ctx = full_bib_tok + CLAIMS_FULL * tail
    print(f"all {CLAIMS_FULL} claims + the complete bibliography, in one context:")
    print(f"  {full_bib_tok:,} + {CLAIMS_FULL} x {tail:.0f} = {single_ctx:,.0f} tokens")
    for m, w in CONTEXT.items():
        fits = "FITS" if single_ctx < w else "does not fit"
        print(f"  {m:20} {w:>9,} window -> {fits}")
    print("\nSo the whole review fits in one Opus or Sonnet context with room to")
    print("spare. Scaling the corpus does NOT force decomposition; only swapping")
    print("abstracts for full texts would, and that corpus is not built.\n")

    print("=" * 74)
    print("COST AT FULL SCALE (608 claims)")
    print("=" * 74)
    out_per_item = {"claude-opus-5": 93, "claude-sonnet-5": 137, "claude-haiku-4-5": 82}
    print(f"{'architecture':34} {'opus-5 low':>12} {'sonnet-5':>11} {'haiku-4.5':>11}")

    def single_context(model, k=1):
        p = pricing.get(model)
        pre = full_bib_tok if model != "claude-haiku-4-5" else full_bib_tok
        t = counts[model]["mean_tail_tokens"]
        reqs = math.ceil(CLAIMS_FULL / k)
        return (pre * p.cache_write_5m / 1e6
                + (reqs - 1) * pre * p.cache_read / 1e6
                + CLAIMS_FULL * t * p.input / 1e6
                + CLAIMS_FULL * out_per_item[model] * p.output / 1e6)

    rows = [("single-context, 1 claim/request", lambda m: single_context(m, 1)),
            ("grouped, 10 claims/request", lambda m: single_context(m, 10)),
            ("grouped, 20 claims/request", lambda m: single_context(m, 20))]
    for name, fn in rows:
        cells = [f"${fn(m):>11.2f}" for m in
                 ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")]
        print(f"{name:34} " + " ".join(cells))

    # orchestrator: Opus plans and merges, workers do chunks
    print()
    print("=" * 74)
    print("ORCHESTRATOR OVERHEAD, COUNTED")
    print("=" * 74)
    op, wk = pricing.get("claude-opus-5"), pricing.get("claude-haiku-4-5")
    for chunks in (4, 8, 16):
        per_chunk = math.ceil(CLAIMS_FULL / chunks)
        # planner: reads plan prompt + a chunk manifest, writes a dispatch per chunk
        plan_cost = (plan_tok + chunks * 40) * op.input / 1e6 + chunks * 60 * op.output / 1e6
        # each worker re-reads its own bibliography slice: no shared cache across models
        slice_tok = full_bib_tok / chunks
        worker = chunks * (slice_tok * wk.cache_write_5m / 1e6
                           + (math.ceil(per_chunk / 10) - 1) * slice_tok * wk.cache_read / 1e6)
        worker += (CLAIMS_FULL * counts["claude-haiku-4-5"]["mean_tail_tokens"] * wk.input / 1e6
                   + CLAIMS_FULL * out_per_item["claude-haiku-4-5"] * wk.output / 1e6)
        merge = (chunks * per_chunk * 12) * op.input / 1e6 + 2000 * op.output / 1e6
        total = plan_cost + worker + merge
        solo = single_context("claude-haiku-4-5", 10)
        print(f"{chunks:>3} chunks: plan ${plan_cost:.3f} + workers ${worker:.3f} "
              f"+ merge ${merge:.3f} = ${total:.3f}"
              f"   (haiku grouped solo: ${solo:.3f})")

    # Isolate what is actually doing the work: scoping, not delegating.
    print()
    print("=" * 74)
    print("WHAT IS THE SAVING ACTUALLY FROM?")
    print("=" * 74)
    solo_full = single_context("claude-haiku-4-5", 10)
    chunks = 16
    slice_tok = full_bib_tok / chunks
    per_chunk = math.ceil(CLAIMS_FULL / chunks)
    sliced_solo = chunks * (slice_tok * wk.cache_write_5m / 1e6
                            + (math.ceil(per_chunk / 10) - 1) * slice_tok * wk.cache_read / 1e6)
    sliced_solo += (CLAIMS_FULL * counts["claude-haiku-4-5"]["mean_tail_tokens"] * wk.input / 1e6
                    + CLAIMS_FULL * out_per_item["claude-haiku-4-5"] * wk.output / 1e6)
    orch_overhead = ((plan_tok + chunks * 40) * op.input / 1e6 + chunks * 60 * op.output / 1e6
                     + (CLAIMS_FULL * 12) * op.input / 1e6 + 2000 * op.output / 1e6)
    print(f"  one agent, whole bibliography every request   ${solo_full:.3f}")
    print(f"  one agent, only the slice each chunk needs    ${sliced_solo:.3f}"
          f"   ({100*(1-sliced_solo/solo_full):.0f}% cheaper)")
    print(f"  the same, but with an orchestrator on top     "
          f"${sliced_solo + orch_overhead:.3f}   (+${orch_overhead:.3f} overhead)")
    print("\nThe saving comes from SCOPING THE CONTEXT, not from delegating. A single")
    print("agent that loads only the references a chunk actually cites captures")
    print("almost all of it. Adding a planner and a merge step on top costs more")
    print("and buys nothing, on a corpus that fits in one context window.")
    print("\nThat is the published finding reproduced on an independent task:")
    print("an orchestrator pays when the work exceeds a context window. This")
    print("corpus does not, so it does not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
