"""End-to-end smoke test on the mock client. Costs nothing.

Exercises prompt assembly, cache accounting, grading, the run log and the
comparison table, and checks that the documented cache effects actually show up
in the numbers.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from sbbench import arms, corpus, grading, pricing, prompts, runlog  # noqa: E402
from sbbench.mockclient import MockAnthropic  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


def build_arms(model_main: str, model_worker: str):
    return [
        ("Z-batch",      arms.ArmConfig("Z-batch", model_main, ttl="1h", batch=True), "sequential"),
        ("A-idle",       arms.ArmConfig("A-idle", model_main), "idle"),
        ("A-nocache",    arms.ArmConfig("A-nocache", model_main, cached=False), "sequential"),
        ("B2-keepalive", arms.ArmConfig("B2-keepalive", model_main), "keepalive"),
        ("C-naive",      arms.ArmConfig("C-naive", model_worker), "fanout"),
        ("C-staggered",  arms.ArmConfig("C-staggered", model_worker), "fanout-staggered"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--worker-model", default="claude-haiku-4-5")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--accuracy", type=float, default=0.85)
    ap.add_argument("--no-abstracts", action="store_true")
    args = ap.parse_args()

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))
    refs_rows = corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))
    refs = {r["key"]: r for r in refs_rows}
    if args.limit:
        pairs = pairs[: args.limit]

    include_abs = not args.no_abstracts
    section_refs = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    prefix = prompts.verify_prefix(section_refs, include_abstract=include_abs)
    prefix_tokens = MockAnthropic().token_counter(prefix)

    print(f"items                : {len(pairs)}")
    print(f"cached prefix        : {len(prefix):,} chars  (~{prefix_tokens:,} est. tokens)")
    for m in (args.model, args.worker_model):
        p = pricing.get(m)
        ok = "caches" if prefix_tokens >= p.min_cacheable_prefix else "TOO SHORT TO CACHE"
        print(f"  {m:20} min prefix {p.min_cacheable_prefix:>5} -> {ok}")
    print()

    log_path = os.path.join(RUNS, "smoke_mock.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)

    rows = []
    with runlog.RunLog(log_path, run_id="smoke", meta={"mock": True}) as log:
        for name, cfg, kind in build_arms(args.model, args.worker_model):
            client = MockAnthropic(accuracy=args.accuracy)
            if kind == "sequential":
                recs = arms.run_sequential(client, cfg, prefix, pairs, refs)
            elif kind == "idle":
                recs = arms.run_idle(client, cfg, prefix, pairs, refs)
            elif kind == "keepalive":
                recs = arms.run_keepalive(client, cfg, prefix, pairs, refs)
            elif kind == "fanout":
                recs = arms.run_fanout(client, cfg, prefix, pairs, refs, staggered=False)
            else:
                recs = arms.run_fanout(client, cfg, prefix, pairs, refs, staggered=True)
            for r in recs:
                r.run_id = "smoke"
                log.add(r)
                rows.append(r.to_dict())

    df = pd.DataFrame(rows)
    graded = df[df["role"] != "keepalive"]

    summary = df.groupby("arm").agg(
        calls=("item_id", "size"),
        cost_usd=("cost_usd", "sum"),
        cache_read=("cache_read_input_tokens", "sum"),
        cache_write=("cache_creation_input_tokens", "sum"),
        uncached_in=("input_tokens", "sum"),
    )
    acc = graded.groupby("arm")["correct"].mean().rename("accuracy")
    n_items = graded.groupby("arm")["item_id"].size().rename("items")
    summary = summary.join(acc).join(n_items)
    summary["usd_per_item"] = summary["cost_usd"] / summary["items"]
    summary = summary.sort_values("cost_usd")

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
    print(summary.to_string())
    print()

    # --- assertions: the documented effects must appear in the numbers -------
    s = summary
    checks = []
    checks.append(("caching beats no-cache",
                   s.loc["A-nocache", "cost_usd"] > s.loc["Z-batch", "cost_usd"]))
    checks.append(("keep-alive beats idle reload",
                   s.loc["B2-keepalive", "cost_usd"] < s.loc["A-idle", "cost_usd"]))
    checks.append(("naive fan-out writes more cache than staggered",
                   s.loc["C-naive", "cache_write"] > s.loc["C-staggered", "cache_write"]))
    checks.append(("staggered fan-out actually reads cache",
                   s.loc["C-staggered", "cache_read"] > 0))
    checks.append(("no-cache arm never writes cache",
                   s.loc["A-nocache", "cache_write"] == 0))
    checks.append(("grader parsed every reply",
                   int(graded["parse_failed"].sum()) == 0))

    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)

    vr = [grading.grade_verify(p, MockAnthropic(accuracy=args.accuracy)
                               .verdict(p["pair_id"], p["label"])) for p in pairs]
    print("\nverification grading breakdown:", grading.summarise_verify(vr))
    print(f"\nrun log: {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
