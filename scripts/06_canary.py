"""Canary: spend a few cents to check the forecast against reality.

Checks, in order of how badly each would distort the full run:
  1. output tokens actually generated (thinking included) vs the assumption
  2. cache forms at all, and is read from request 2 onward
  3. predicted vs actual cost for the items run
  4. parse rate on real replies
  5. prefix bytes identical across requests

Prints a hard cost ceiling before calling anything and refuses to exceed it.
"""

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, grading, live, pricing, prompts, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--ttl", default="5m")
    ap.add_argument("--assumed-output", type=int, default=120)
    ap.add_argument("--ceiling", type=float, default=0.60, help="abort above this USD")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation gate")
    args = ap.parse_args()

    problems = live.sanity_check_config(args.model, args.effort)
    if problems:
        for p in problems:
            print("CONFIG ERROR:", p)
        return 2

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))[: args.n]
    refs_all = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}
    refs_section = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    prefix = prompts.verify_prefix(refs_section)

    price = pricing.get(args.model)

    # ---- forecast, before spending anything --------------------------------
    import json
    tc_path = os.path.join(DATA, "token_counts.json")
    tc = json.load(open(tc_path, encoding="utf-8"))["counts"][args.model]
    pre_tok, tail_tok = tc["prefix_tokens"], tc["mean_tail_tokens"]

    predicted = pricing.cost(
        args.model,
        input_tokens=int(tail_tok * args.n),
        output_tokens=args.assumed_output * args.n,
        cache_creation_tokens=pre_tok,
        cache_read_tokens=int(pre_tok * (args.n - 1)),
        ttl=args.ttl,
    )
    # Worst realistic case: cache never forms, replies run to max_tokens.
    ceiling_cost = pricing.cost(
        args.model,
        input_tokens=int((pre_tok + tail_tok) * args.n),
        output_tokens=args.max_tokens * args.n,
    )

    print(f"model            : {args.model}   effort={args.effort or 'default'}")
    print(f"items            : {args.n}")
    print(f"prefix / tail    : {pre_tok:,} / {tail_tok:.0f} tokens")
    print(f"cacheable        : {pre_tok >= price.min_cacheable_prefix} "
          f"(minimum {price.min_cacheable_prefix:,})")
    print()
    print(f"PREDICTED cost   : ${predicted:.4f}")
    print(f"WORST CASE       : ${ceiling_cost:.4f}  (no cache, all replies hit max_tokens)")

    if ceiling_cost > args.ceiling:
        print(f"\nABORT: worst case exceeds the ${args.ceiling:.2f} ceiling.")
        return 3
    if not args.yes:
        print(f"\nRe-run with --yes to spend up to ${ceiling_cost:.4f}.")
        return 0

    # ---- run ---------------------------------------------------------------
    cli = live.LiveAnthropic(max_tokens=args.max_tokens, effort=args.effort)
    log_path = os.path.join(RUNS, f"canary_{args.model}.jsonl")
    results, rows = [], []
    prefix_hash = hashlib.sha256(prefix.encode()).hexdigest()[:12]

    print(f"\nprefix sha256[:12] = {prefix_hash}\n")
    with runlog.RunLog(log_path, run_id=f"canary-{args.model}",
                       meta={"mock": False, "effort": args.effort}) as log:
        for i, p in enumerate(pairs, 1):
            tail = prompts.verify_tail(p["claim_text"], refs_all[p["candidate_key"]])
            r = cli.create(args.model, prefix, tail, ttl=args.ttl)
            g = grading.grade_verify(p, r.text)
            cost = pricing.cost_from_usage(args.model, r.usage, ttl=args.ttl)
            results.append(g)
            rows.append({"usage": r.usage, "cost": cost, "stop": r.stop_reason,
                         "think": r.thinking_chars, "wall": r.wall_seconds,
                         "text": r.text})
            log.add(runlog.RunRecord(
                run_id=f"canary-{args.model}", arm="canary", task="verify",
                model=args.model, item_id=p["pair_id"], role="solo",
                cost_usd=cost, wall_seconds=r.wall_seconds, effort=args.effort,
                ttl=args.ttl, mock=False, correct=g.correct, predicted=g.predicted,
                truth=g.truth, difficulty=g.difficulty, parse_failed=g.parse_failed,
                notes=r.stop_reason, **r.usage,
            ))
            u = r.usage
            print(f"  [{i}/{len(pairs)}] in={u['input_tokens']:>5} "
                  f"write={u['cache_creation_input_tokens']:>6} "
                  f"read={u['cache_read_input_tokens']:>6} "
                  f"out={u['output_tokens']:>5}  "
                  f"${cost:.4f}  {r.stop_reason:<10} "
                  f"{'OK' if not g.parse_failed else 'PARSE-FAIL'}")

    # ---- report ------------------------------------------------------------
    actual = sum(x["cost"] for x in rows)
    out_mean = sum(x["usage"]["output_tokens"] for x in rows) / len(rows)
    reads = [x["usage"]["cache_read_input_tokens"] for x in rows]
    writes = [x["usage"]["cache_creation_input_tokens"] for x in rows]

    print("\n--- forecast vs reality " + "-" * 40)
    print(f"cost            predicted ${predicted:.4f}   actual ${actual:.4f}   "
          f"ratio {actual / predicted:.2f}x" if predicted else "")
    print(f"output tokens   assumed {args.assumed_output}   actual mean {out_mean:.0f}   "
          f"ratio {out_mean / args.assumed_output:.2f}x")
    print(f"cache write     request 1 = {writes[0]:,} (prefix is {pre_tok:,})")
    print(f"cache reads     {reads[1:]}")
    print(f"parse failures  {sum(r.parse_failed for r in results)}/{len(results)}")
    print(f"stop reasons    {sorted({x['stop'] for x in rows})}")
    thinking = sum(x["think"] for x in rows)
    print(f"thinking text   {thinking} chars returned")

    print("\n--- checks " + "-" * 53)
    checks = [
        ("cache formed on request 1", writes[0] > 0),
        ("cache read on every later request", all(r > 0 for r in reads[1:]) if len(reads) > 1 else None),
        ("write matches counted prefix (within 2%)", abs(writes[0] - pre_tok) / pre_tok < 0.02 if writes[0] else False),
        ("no reply truncated at max_tokens", all(x["stop"] != "max_tokens" for x in rows)),
        ("every reply parsed", sum(r.parse_failed for r in results) == 0),
    ]
    ok = True
    for label, passed in checks:
        if passed is None:
            print(f"  [SKIP] {label}")
            continue
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)

    # ---- where the money goes ---------------------------------------------
    # The API folds thinking into output_tokens. Counting the visible reply
    # (free) and subtracting recovers the split.
    from sbbench import counting
    ctr = counting.Counter(args.model)
    answer_tok = [ctr.count(x["text"]) if x["text"] else 0 for x in rows]
    mean_answer = sum(answer_tok) / len(answer_tok)
    mean_think = max(0.0, out_mean - mean_answer)

    p = price
    per_item = {
        "cache read (bibliography)": (sum(reads) / len(rows)) * p.cache_read / 1e6,
        "uncached input (the claim)": (sum(x["usage"]["input_tokens"] for x in rows) / len(rows)) * p.input / 1e6,
        "thinking": mean_think * p.output / 1e6,
        "answer text": mean_answer * p.output / 1e6,
    }
    total_pi = sum(per_item.values()) or 1.0
    print("\n--- where the money goes (mean per item, steady state) " + "-" * 8)
    for k, v in per_item.items():
        print(f"  {k:28} ${v:.5f}   {100 * v / total_pi:5.1f}%")
    print(f"  {'TOTAL':28} ${total_pi:.5f}")
    print(f"\n  output split: {mean_answer:.0f} answer tokens + "
          f"{mean_think:.0f} thinking tokens = {out_mean:.0f}")

    bd_path = os.path.join(DATA, f"breakdown_{args.model}_{args.effort or 'default'}.json")
    json.dump({"model": args.model, "effort": args.effort, "n": len(rows),
               "mean_output_tokens": out_mean, "mean_answer_tokens": mean_answer,
               "mean_thinking_tokens": mean_think, "per_item_usd": per_item},
              open(bd_path, "w", encoding="utf-8"), indent=2)

    scale = out_mean / args.assumed_output if args.assumed_output else 1
    full = pricing.cost(
        args.model,
        input_tokens=int(tail_tok * 117),
        output_tokens=int(out_mean * 117),
        cache_creation_tokens=pre_tok,
        cache_read_tokens=int(pre_tok * 116),
        ttl=args.ttl,
    )
    print(f"\nre-forecast for a full 117-item pass on {args.model}: ${full:.3f}")
    print(f"  (output-token assumption was off by {scale:.2f}x)")
    print(f"\nrun log: {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
