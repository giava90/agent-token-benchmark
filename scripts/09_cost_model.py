"""Free cost model: everything about this benchmark that is arithmetic.

No API calls. Derives from measured token counts (data/token_counts.json) and
measured per-item output tokens (runs/full_run.jsonl) the answers that do not
require behaviour:

  1. idle gaps - lapse vs keep-alive ping vs 1-hour TTL (architecture A / B)
  2. items per request - amortising the prefix across a batched prompt
  3. Batch API, under both cache branches

Caching is a billing transformation, so these are exact given the token counts,
in the same way the uncached baseline was.
"""

import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import pricing, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

N = 117
TTL_5M, TTL_1H = 300.0, 3600.0
PING_EVERY = 240.0          # comfortably inside the 5-minute TTL


def measured_outputs() -> dict:
    rows = runlog.load(os.path.join(RUNS, "full_run.jsonl"))
    agg = defaultdict(list)
    for r in rows:
        agg[r["arm"]].append(r)
    return {a: {"model": rs[0]["model"],
                "out": sum(r["output_tokens"] for r in rs) / len(rs),
                "wall": sum(r["wall_seconds"] for r in rs) / len(rs)}
            for a, rs in agg.items()}


def main() -> int:
    counts = json.load(open(os.path.join(DATA, "token_counts.json"), encoding="utf-8"))["counts"]
    meas = measured_outputs()

    print("=" * 72)
    print("1. IDLE GAPS - what does letting the cache lapse actually cost?")
    print("=" * 72)
    print("Per lapse you pay one extra cache write. Per keep-alive ping you pay")
    print("one cache read. The 1-hour TTL pays a higher write once, then nothing.\n")

    for arm in ("opus-low", "sonnet-default", "haiku"):
        model = meas[arm]["model"]
        p = pricing.get(model)
        pre = counts[model]["prefix_tokens"]
        write5 = pre * p.cache_write_5m / 1e6
        write1h = pre * p.cache_write_1h / 1e6
        read = pre * p.cache_read / 1e6
        print(f"{model}  (prefix {pre:,} tokens)")
        print(f"   one cache write, 5m ttl : ${write5:.4f}")
        print(f"   one cache write, 1h ttl : ${write1h:.4f}   (+${write1h-write5:.4f} once)")
        print(f"   one cache read / ping   : ${read:.4f}")
        print(f"   pings that equal one lapse: {write5/read:.1f}"
              f"  -> keep-alive is cheaper than lapsing for gaps under "
              f"{write5/read*PING_EVERY/60:.0f} min")
        print()

    print(f"{'gap':>8} {'gaps':>5} | {'A: lapse':>10} {'B2: ping':>10} {'B3: 1h ttl':>11}  cheapest")
    model = meas["opus-low"]["model"]
    p = pricing.get(model)
    pre = counts[model]["prefix_tokens"]
    write5, write1h, read = (pre * p.cache_write_5m / 1e6, pre * p.cache_write_1h / 1e6,
                             pre * p.cache_read / 1e6)
    for gap_min in (10, 20, 30, 45, 60, 120):
        gap = gap_min * 60
        for k in (1, 3, 10):
            lapse = k * write5
            pings = k * math.ceil(gap / PING_EVERY) * read
            # 1h ttl: pay the premium once; only lapses if the gap exceeds an hour
            ttl1h = (write1h - write5) + (k * write5 if gap > TTL_1H else 0.0)
            best = min((lapse, "lapse"), (pings, "ping"), (ttl1h, "1h ttl"))[1]
            print(f"{gap_min:>6}m {k:>5} | {lapse:>10.4f} {pings:>10.4f} {ttl1h:>11.4f}  {best}")
    print()
    base = 2.677
    print(f"For scale: a full {N}-item pass on {model} costs ${base:.3f}.")
    print(f"Three 30-minute idle gaps add ${3*write5:.3f} if you let them lapse "
          f"({100*3*write5/base:.1f}% of the pass), or ${(write1h-write5):.3f} "
          f"({100*(write1h-write5)/base:.1f}%) on the 1-hour TTL.")

    print()
    print("=" * 72)
    print("2. ITEMS PER REQUEST - amortising the prefix")
    print("=" * 72)
    print("The prefix is re-read once per request. Judging k claims per request")
    print("pays that read once for k items.\n")

    print(f"{'items/req':>10} {'requests':>9} | " +
          "  ".join(f"{a:>14}" for a in ("opus-low", "sonnet-default", "haiku")))
    curves = {}
    for k in (1, 2, 5, 10, 20, 40, 117):
        cells = []
        for arm in ("opus-low", "sonnet-default", "haiku"):
            model = meas[arm]["model"]
            p = pricing.get(model)
            pre = counts[model]["prefix_tokens"]
            tail = counts[model]["mean_tail_tokens"]
            out = meas[arm]["out"]
            reqs = math.ceil(N / k)
            cost = (pre * p.cache_write_5m / 1e6
                    + (reqs - 1) * pre * p.cache_read / 1e6
                    + N * tail * p.input / 1e6
                    + N * out * p.output / 1e6)
            curves.setdefault(arm, {})[k] = cost
            cells.append(f"${cost:>13.3f}")
        print(f"{k:>10} {math.ceil(N/k):>9} | " + "  ".join(cells))

    print()
    for arm in ("opus-low", "sonnet-default", "haiku"):
        c1, c10 = curves[arm][1], curves[arm][10]
        print(f"  {arm:16} 10 per request cuts cost {100*(1-c10/c1):.0f}%  "
              f"(${c1:.3f} -> ${c10:.3f})")

    print()
    print("=" * 72)
    print("3. BATCH API - the two branches")
    print("=" * 72)
    print("Batch is 50% off every token. But it is submitted all at once, so the")
    print("send-one-then-fan-out pattern is impossible and cache hits are")
    print("documented as best-effort.\n")

    print(f"{'configuration':16} {'live cached':>12} {'batch, cache':>13} {'batch, no cache':>16}")
    for arm in ("opus-low", "opus-default", "sonnet-default", "haiku"):
        model = meas[arm]["model"]
        p = pricing.get(model)
        pre, tail, out = (counts[model]["prefix_tokens"],
                          counts[model]["mean_tail_tokens"], meas[arm]["out"])
        live = (pre * p.cache_write_5m / 1e6 + (N - 1) * pre * p.cache_read / 1e6
                + N * tail * p.input / 1e6 + N * out * p.output / 1e6)
        b_cache = 0.5 * (pre * p.cache_write_1h / 1e6 + (N - 1) * pre * p.cache_read / 1e6
                         + N * tail * p.input / 1e6 + N * out * p.output / 1e6)
        b_nocache = 0.5 * (N * (pre + tail) * p.input / 1e6 + N * out * p.output / 1e6)
        print(f"{arm:16} {live:>12.3f} {b_cache:>13.3f} {b_nocache:>16.3f}")
    print("\nIf cache reads land, batch roughly halves the bill. If they do not,")
    print("batch costs several times more than the live cached run it replaces.")
    print("One 10-item Haiku batch settles which, for about two cents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
