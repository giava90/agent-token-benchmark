"""The full experiment: 5 configurations x 117 items.

Staggered fan-out: send one request to write the cache, wait for it to return,
then parallelise the rest so they all read it. Firing everything at once would
make 117 cache writes and zero reads - the effect this study measures.

Resumable: items already present in the run log for a configuration are skipped,
so a crash never means paying twice.
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, grading, live, pricing, prompts, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

CONFIGS = [
    ("opus-default",   "claude-opus-5",    None),
    ("opus-low",       "claude-opus-5",    "low"),
    ("sonnet-default", "claude-sonnet-5",  None),
    ("sonnet-low",     "claude-sonnet-5",  "low"),
    ("haiku",          "claude-haiku-4-5", None),
]


def done_items(path: str, arm: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    return {r["item_id"] for r in runlog.load(path) if r["arm"] == arm}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=117)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--ceiling", type=float, default=14.0)
    ap.add_argument("--only", default=None, help="run one configuration by name")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))[: args.n]
    refs_all = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}
    refs_section = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    prefix = prompts.verify_prefix(refs_section)
    counts = json.load(open(os.path.join(DATA, "token_counts.json"), encoding="utf-8"))["counts"]

    configs = [c for c in CONFIGS if args.only is None or c[0] == args.only]
    log_path = os.path.join(RUNS, "full_run.jsonl")

    # Forecast from the canary's measured output tokens where available.
    measured_out = {}
    for name, model, effort in configs:
        bd = os.path.join(DATA, f"breakdown_{model}_{effort or 'default'}.json")
        measured_out[name] = (json.load(open(bd, encoding="utf-8"))["mean_output_tokens"]
                              if os.path.exists(bd) else 200.0)

    total_pred = 0.0
    print(f"{'configuration':18} {'model':18} {'effort':8} {'pred $':>8}")
    for name, model, effort in configs:
        c = counts[model]
        pred = pricing.cost(
            model,
            input_tokens=int(c["mean_tail_tokens"] * len(pairs)),
            output_tokens=int(measured_out[name] * len(pairs)),
            cache_creation_tokens=c["prefix_tokens"],
            cache_read_tokens=int(c["prefix_tokens"] * (len(pairs) - 1)),
        )
        total_pred += pred
        print(f"{name:18} {model:18} {effort or 'default':8} {pred:8.3f}")
    print(f"{'':46} {total_pred:8.3f}  TOTAL PREDICTED")

    if total_pred > args.ceiling:
        print(f"\nABORT: forecast exceeds the ${args.ceiling:.2f} ceiling.")
        return 3
    if not args.yes:
        print(f"\nRe-run with --yes to spend about ${total_pred:.2f}.")
        return 0

    lock = threading.Lock()
    grand_total = 0.0

    with runlog.RunLog(log_path, run_id="full", meta={"mock": False, "n": len(pairs)}) as log:
        for name, model, effort in configs:
            already = done_items(log_path, name)
            todo = [p for p in pairs if p["pair_id"] not in already]
            if not todo:
                print(f"\n{name}: already complete, skipping")
                continue
            print(f"\n{name}  ({len(todo)} items to run, {len(already)} already done)")

            cli = live.LiveAnthropic(max_tokens=args.max_tokens, effort=effort)
            spent = [0.0]

            def run_one(p):
                tail = prompts.verify_tail(p["claim_text"], refs_all[p["candidate_key"]])
                r = cli.create(model, prefix, tail)
                g = grading.grade_verify(p, r.text)
                cost = pricing.cost_from_usage(model, r.usage)
                with lock:
                    spent[0] += cost
                    log.add(runlog.RunRecord(
                        run_id="full", arm=name, task="verify", model=model,
                        item_id=p["pair_id"], role="solo", cost_usd=cost,
                        wall_seconds=r.wall_seconds, effort=effort, mock=False,
                        correct=g.correct, predicted=g.predicted, truth=g.truth,
                        difficulty=g.difficulty, parse_failed=g.parse_failed,
                        notes=r.stop_reason, **r.usage,
                    ))
                return g

            # One request first, so the cache exists before the fan-out.
            results = [run_one(todo[0])]
            if len(todo) > 1:
                with ThreadPoolExecutor(max_workers=args.workers) as ex:
                    results += list(ex.map(run_one, todo[1:]))

            s = grading.summarise_verify(results)
            lo, hi = s["ci95_low"], s["ci95_high"]
            print(f"  accuracy {s['accuracy']:.3f}  95% CI [{lo:.3f}, {hi:.3f}]"
                  f"  parse-fail {s['parse_failures']}  cost ${spent[0]:.3f}")
            grand_total += spent[0]

    print(f"\nTOTAL SPENT THIS INVOCATION: ${grand_total:.3f}")
    print(f"run log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
