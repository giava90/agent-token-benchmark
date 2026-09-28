"""Does judging k claims per request cost less without costing accuracy?

The cost model says 10 claims per request cuts the bill ~70%, because the 37k
bibliography is re-read once per request rather than once per claim. That half is
arithmetic. What arithmetic cannot say is whether judging ten claims in one pass
is as accurate as judging them one at a time.

Same items, same prefix, same model - only the grouping changes.
"""

import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, counting, grading, live, pricing, prompts, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

SYSTEM_MULTI = """You check whether citations in a scientific review are correctly placed.

The bibliography below is your reference library. You will be given several
numbered items. Each has a claim sentence, with [CITATION] where its reference
was removed, and the citation key of one candidate reference. For each item,
look the key up in the bibliography and decide whether the work it names
supports that claim.

Answer with one line per item, in order, in exactly this format:

1. SUPPORTED
2. NOT_SUPPORTED

Give no other text."""


def multi_prefix(refs, include_abstract=True):
    return SYSTEM_MULTI + "\n\nBIBLIOGRAPHY\n\n" + prompts.bibliography_block(refs, include_abstract)


def multi_tail(group, refs):
    parts = []
    for i, p in enumerate(group, 1):
        parts.append(f"{i}. CLAIM: {p['claim_text']}\n"
                     f"   CANDIDATE CITATION KEY: [{p['candidate_key']}]")
    return "ITEMS\n\n" + "\n\n".join(parts) + f"\n\nGive {len(group)} verdicts, one per line."


LINE = re.compile(r"^\s*(\d+)\s*[.):]?\s*(NOT[_ ]?SUPPORTED|SUPPORTED)", re.I | re.M)


def parse_multi(text, n):
    """Return a list of n verdicts, None where unparseable."""
    out = [None] * n
    for m in LINE.finditer(text or ""):
        idx = int(m.group(1)) - 1
        if 0 <= idx < n:
            tok = m.group(2).upper().replace(" ", "_")
            out[idx] = "not_supported" if tok.startswith("NOT") else "supported"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--k", type=int, default=10, help="claims per request")
    ap.add_argument("--n", type=int, default=60, help="items to cover")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--ceiling", type=float, default=1.50)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))[: args.n]
    refs_all = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}
    section = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    prefix = multi_prefix(section)

    groups = [pairs[i:i + args.k] for i in range(0, len(pairs), args.k)]
    p = pricing.get(args.model)
    ctr = counting.Counter(args.model)
    pre_tok = ctr.count(prefix)
    tail_toks = [ctr.count(multi_tail(g, refs_all)) for g in groups]

    predicted = pricing.cost(
        args.model, input_tokens=sum(tail_toks),
        output_tokens=int(12 * len(pairs)),          # ~12 tokens per verdict line
        cache_creation_tokens=pre_tok,
        cache_read_tokens=pre_tok * (len(groups) - 1),
    )
    worst = pricing.cost(args.model,
                         input_tokens=pre_tok * len(groups) + sum(tail_toks),
                         output_tokens=args.max_tokens * len(groups))
    print(f"{args.model}  k={args.k}  items={len(pairs)}  requests={len(groups)}")
    print(f"  prefix {pre_tok:,} tokens, mean grouped tail {sum(tail_toks)/len(tail_toks):.0f}")
    print(f"  PREDICTED ${predicted:.4f}   WORST CASE ${worst:.4f}")
    if worst > args.ceiling:
        print(f"ABORT: worst case above ${args.ceiling:.2f} ceiling")
        return 3
    if not args.yes:
        print("\nRe-run with --yes.")
        return 0

    cli = live.LiveAnthropic(max_tokens=args.max_tokens, effort=args.effort)
    results, total, parse_fail = [], 0.0, 0
    log_path = os.path.join(RUNS, f"multi_item_{args.model}_k{args.k}.jsonl")

    with runlog.RunLog(log_path, run_id=f"multi-k{args.k}",
                       meta={"mock": False, "k": args.k}) as log:
        for gi, group in enumerate(groups):
            r = cli.create(args.model, prefix, multi_tail(group, refs_all))
            verdicts = parse_multi(r.text, len(group))
            cost = pricing.cost_from_usage(args.model, r.usage)
            total += cost
            share = cost / len(group)
            for p_item, v in zip(group, verdicts):
                ok = (v == p_item["label"])
                if v is None:
                    parse_fail += 1
                results.append(grading.VerifyResult(
                    pair_id=p_item["pair_id"], predicted=v, truth=p_item["label"],
                    correct=ok, difficulty=p_item["difficulty"], parse_failed=(v is None)))
                log.add(runlog.RunRecord(
                    run_id=f"multi-k{args.k}", arm=f"multi-k{args.k}", task="verify",
                    model=args.model, item_id=p_item["pair_id"], role="solo",
                    cost_usd=share, wall_seconds=r.wall_seconds / len(group),
                    effort=args.effort, mock=False, correct=ok, predicted=v,
                    truth=p_item["label"], difficulty=p_item["difficulty"],
                    parse_failed=(v is None), notes=f"group {gi} of {len(groups)}",
                    **r.usage))
            u = r.usage
            print(f"  req {gi+1}/{len(groups)}  read={u['cache_read_input_tokens']:>6} "
                  f"out={u['output_tokens']:>4}  ${cost:.4f}  "
                  f"parsed {sum(1 for v in verdicts if v)}/{len(group)}")

    s = grading.summarise_verify(results)
    print(f"\ngrouped k={args.k}: accuracy {s['accuracy']:.3f} "
          f"95% CI [{s['ci95_low']:.3f}, {s['ci95_high']:.3f}]  "
          f"parse-fail {parse_fail}  cost ${total:.4f}")

    # compare against the same items judged one at a time in the full run
    full = {r["item_id"]: r for r in runlog.load(os.path.join(RUNS, "full_run.jsonl"))
            if r["arm"] == ("haiku" if args.model == "claude-haiku-4-5" else "")}
    ids = [r.pair_id for r in results if r.pair_id in full]
    if ids:
        single_hits = sum(1 for i in ids if full[i]["correct"])
        single_cost = sum(full[i]["cost_usd"] for i in ids)
        b = sum(1 for i in ids if dict((r.pair_id, r.correct) for r in results)[i]
                and not full[i]["correct"])
        c = sum(1 for i in ids if not dict((r.pair_id, r.correct) for r in results)[i]
                and full[i]["correct"])
        import math as _m
        n_disc = b + c
        pval = (min(1.0, 2 * sum(_m.comb(n_disc, j) for j in range(0, min(b, c) + 1)) / 2 ** n_disc)
                if n_disc else 1.0)
        print(f"same {len(ids)} items, one per request: accuracy {single_hits/len(ids):.3f} "
              f"cost ${single_cost:.4f}")
        print(f"grouped better on {b}, worse on {c}, McNemar p={pval:.3f}")
        print(f"cost per item: grouped ${total/len(results):.5f} vs "
              f"single ${single_cost/len(ids):.5f}  "
              f"({100*(1-(total/len(results))/(single_cost/len(ids))):.0f}% cheaper)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
