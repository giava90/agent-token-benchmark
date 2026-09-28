"""Does prompt caching work inside a concurrent Batch API submission?

The whole batch question hangs on this. Batch is 50% off every token, but it is
submitted all at once, so nothing can wait for a first response before the rest
start - the pattern that produced 116 cache reads per configuration in the live
run is impossible here. The docs call in-batch cache hits best-effort.

Ten Haiku items, about two cents, settles it.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, counting, pricing, prompts  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--ttl", default="1h", help="docs recommend 1h for batches")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))[: args.n]
    refs = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}
    section = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    prefix = prompts.verify_prefix(section)

    p = pricing.get(args.model)
    pre = json.load(open(os.path.join(DATA, "token_counts.json"), encoding="utf-8"))["counts"][args.model]["prefix_tokens"]
    if_cache = 0.5 * (pre * p.cache_write_1h + (args.n - 1) * pre * p.cache_read) / 1e6
    if_not = 0.5 * (args.n * pre * p.input) / 1e6
    print(f"{args.n} items on {args.model}, batch, {args.ttl} ttl")
    print(f"  if cache reads land : ~${if_cache:.4f}")
    print(f"  if they do not      : ~${if_not:.4f}   ({if_not/if_cache:.1f}x more)")
    if not args.yes:
        print("\nRe-run with --yes.")
        return 0

    client = counting.client()
    cc = {"type": "ephemeral"}
    if args.ttl == "1h":
        cc["ttl"] = "1h"

    requests = []
    for pr in pairs:
        requests.append({
            "custom_id": pr["pair_id"],
            "params": {
                "model": args.model,
                "max_tokens": args.max_tokens,
                "system": [{"type": "text", "text": prefix, "cache_control": cc}],
                "messages": [{"role": "user",
                              "content": prompts.verify_tail(pr["claim_text"],
                                                             refs[pr["candidate_key"]])}],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    print(f"\nsubmitted batch {batch.id}")

    t0 = time.time()
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        if time.time() - t0 > args.timeout:
            print(f"still {b.processing_status} after {args.timeout}s — check later with id above")
            return 1
        print(f"  {b.processing_status}  {int(time.time()-t0)}s  counts={b.request_counts}")
        time.sleep(20)
    print(f"ended after {int(time.time()-t0)}s")

    rows, total = [], 0.0
    for res in client.messages.batches.results(batch.id):
        if res.result.type != "succeeded":
            print(f"  {res.custom_id}: {res.result.type}")
            continue
        u = res.result.message.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        }
        cost = pricing.cost_from_usage(args.model, usage, ttl=args.ttl, batch=True)
        total += cost
        rows.append({"custom_id": res.custom_id, **usage, "cost_usd": cost})

    reads = sum(1 for r in rows if r["cache_read_input_tokens"] > 0)
    writes = sum(1 for r in rows if r["cache_creation_input_tokens"] > 0)
    print(f"\nresults: {len(rows)}")
    print(f"  requests that READ the cache  : {reads}/{len(rows)}")
    print(f"  requests that WROTE the cache : {writes}/{len(rows)}")
    print(f"  actual cost                   : ${total:.4f}")
    print(f"\nVERDICT: in-batch caching "
          f"{'WORKS — ' + str(reads) + ' of ' + str(len(rows)) + ' read the prefix' if reads else 'DID NOT HAPPEN — every item paid full price'}")

    out = os.path.join(RUNS, "batch_probe.jsonl")
    os.makedirs(RUNS, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
