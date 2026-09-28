"""Layer 2: exact token counts and the predicted-cost table.

Uses only the free count_tokens endpoint - no message creation, no spend.
Replaces the char/4 estimates from the mock with real per-model counts, then
prices every arm from them.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from sbbench import corpus, counting, pricing, prompts  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--tail-sample", type=int, default=15,
                    help="tails to count per model; the mean is used for the rest")
    ap.add_argument("--output-tokens", type=int, default=120,
                    help="assumed reply length; calibrate in the paid pilot")
    ap.add_argument("--no-abstracts", action="store_true")
    args = ap.parse_args()

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))
    refs_all = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}
    refs_section = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))

    include_abs = not args.no_abstracts
    prefix = prompts.verify_prefix(refs_section, include_abstract=include_abs)
    sample = pairs[: args.tail_sample]

    print(f"items            : {len(pairs)}")
    print(f"prefix           : {len(prefix):,} chars")
    print(f"tail sample      : {len(sample)} of {len(pairs)}")
    print(f"assumed output   : {args.output_tokens} tokens/reply\n")

    counts = {}
    for model in args.models:
        try:
            c = counting.Counter(model)
            prefix_tokens = c.count(prefix)
            tails = [
                c.count(prompts.verify_tail(p["claim_text"], refs_all[p["candidate_key"]],
                                            include_abstract=include_abs))
                for p in sample
            ]
        except Exception as exc:  # noqa: BLE001
            print(counting.explain_auth_error(exc))
            return 2

        mean_tail = sum(tails) / len(tails)
        p = pricing.get(model)
        caches = prefix_tokens >= p.min_cacheable_prefix
        counts[model] = {
            "prefix_tokens": prefix_tokens,
            "mean_tail_tokens": mean_tail,
            "min_tail": min(tails),
            "max_tail": max(tails),
            "min_cacheable_prefix": p.min_cacheable_prefix,
            "caches": caches,
            "count_calls": c.calls,
        }
        flag = "caches" if caches else "TOO SHORT TO CACHE"
        print(f"{model:20} prefix {prefix_tokens:>8,}  tail mean {mean_tail:>6.0f} "
              f"(min {min(tails)}, max {max(tails)})  -> {flag}")

    # Cross-model tokenizer comparison on identical bytes.
    base = args.models[0]
    print(f"\ntokenizer ratio vs {base} on the same prefix:")
    for m in args.models:
        r = counts[m]["prefix_tokens"] / counts[base]["prefix_tokens"]
        print(f"  {m:20} {r:5.2f}x")

    # --- predicted cost per arm --------------------------------------------
    n = len(pairs)
    rows = []
    for model in args.models:
        c = counts[model]
        pre, tail = c["prefix_tokens"], c["mean_tail_tokens"]
        out = args.output_tokens

        def price(**kw):
            return pricing.cost(model, output_tokens=out * n, **kw)

        cached_ttl5 = pricing.cost(
            model, input_tokens=int(tail * n), output_tokens=out * n,
            cache_creation_tokens=pre, cache_read_tokens=int(pre * (n - 1)),
        )
        cached_batch = pricing.cost(
            model, input_tokens=int(tail * n), output_tokens=out * n,
            cache_creation_tokens=pre, cache_read_tokens=int(pre * (n - 1)),
            ttl="1h", batch=True,
        )
        uncached = pricing.cost(
            model, input_tokens=int((pre + tail) * n), output_tokens=out * n,
        )
        # One idle gap past the TTL costs exactly one extra cache write.
        idle = cached_ttl5 + pricing.cost(model, cache_creation_tokens=pre)

        rows.append({
            "model": model,
            "uncached (derived)": uncached,
            "cached 5m": cached_ttl5,
            "cached + 1 idle reload": idle,
            "cached 1h + batch": cached_batch,
            "cache ratio": uncached / cached_ttl5 if cached_ttl5 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("model")
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.3f}")
    print(f"\npredicted cost for one full pass over {n} items (USD):")
    print(df.to_string())

    out_path = os.path.join(DATA, "token_counts.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "n_items": n,
            "assumed_output_tokens": args.output_tokens,
            "include_abstracts": include_abs,
            "counts": counts,
            "predicted_cost_usd": df.reset_index().to_dict(orient="records"),
        }, fh, indent=2)
    print(f"\nwrote {out_path}")
    print("All calls used count_tokens, which is free. Nothing was billed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
