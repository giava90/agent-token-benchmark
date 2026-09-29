"""Grade the agent-harness runs against the gold key.

One subagent per model, identical prompt, identical 40-item balanced slice from
the CORRECTED (shuffled-label) benchmark. The slice and bibliography live in
per-model directories outside the repository so that no agent can reach
data/pairs.jsonl, which contains the labels.

Accuracy here is NOT comparable to the API runs. A subagent retrieves what it
needs from files with tools; the API arm received a fixed cached prefix and one
item per request. Different information, different task. What this compares is
models against each other within the agent harness.
"""

import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, grading  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
SCRATCH = (r"C:\Users\giacomov\AppData\Local\Temp\claude"
           r"\c--Users-giacomov-OneDrive---ETH-Zurich-Dokumente-Experimenting-"
           r"tuttorialForAgenticFlowOptimization"
           r"\6bdcced2-369b-4dcf-8dfd-2d741af3eaf7\scratchpad")
MODELS = ["haiku", "sonnet", "opus"]


def binom_two_sided(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def main() -> int:
    key = json.load(open(os.path.join(SCRATCH, "agent_eval_key.json"), encoding="utf-8"))
    pairs = {p["pair_id"]: p for p in corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))}
    tiers = {i: pairs[i]["difficulty"] for i in key}

    got = {}
    for m in MODELS:
        path = os.path.join(SCRATCH, "agent_eval", m, "verdicts.json")
        if not os.path.exists(path):
            print(f"{m}: no verdicts.json yet — still running?")
            continue
        # utf-8-sig: an agent may write the file with a BOM.
        got[m] = json.load(open(path, encoding="utf-8-sig"))

    if not got:
        return 1

    print(f"agent harness, {len(key)} items (20 supported / 20 not), "
          f"corrected shuffled-label benchmark\n")
    print(f"{'model':8} {'n':>4} {'accuracy':>9} {'95% CI':>16} "
          f"{'positives':>10} {'easy neg':>9} {'hard neg':>9} {'said sup':>9}")
    summary = {}
    for m in MODELS:
        if m not in got:
            continue
        v = got[m]
        ids = [i for i in key if i in v]
        hits = sum(1 for i in ids if v[i] == key[i])
        lo, hi = grading.wilson_interval(hits, len(ids))
        cells = {}
        for t in ("positive", "easy", "hard"):
            sub = [i for i in ids if tiers[i] == t]
            cells[t] = sum(1 for i in sub if v[i] == key[i]) / len(sub) if sub else float("nan")
        said_sup = sum(1 for i in ids if v[i] == "supported")
        summary[m] = {"n": len(ids), "accuracy": hits / len(ids), "ci95": [lo, hi],
                      "by_tier": cells, "said_supported": said_sup,
                      "missing": [i for i in key if i not in v]}
        print(f"{m:8} {len(ids):>4} {hits/len(ids):>9.3f} [{lo:.3f},{hi:.3f}] "
              f"{cells['positive']:>10.2f} {cells['easy']:>9.2f} {cells['hard']:>9.2f} "
              f"{said_sup:>9}/{len(ids)}")

    present = [m for m in MODELS if m in got]
    if len(present) > 1:
        print("\npaired comparisons (McNemar exact, same items)")
        for i, a in enumerate(present):
            for b in present[i + 1:]:
                ids = [x for x in key if x in got[a] and x in got[b]]
                ao = sum(1 for x in ids if got[a][x] == key[x] and got[b][x] != key[x])
                bo = sum(1 for x in ids if got[a][x] != key[x] and got[b][x] == key[x])
                p = binom_two_sided(ao, bo)
                print(f"  {a:8} vs {b:8}  {a} only {ao:3}  {b} only {bo:3}  p={p:.4f}"
                      f"  {'significant' if p < 0.05 else 'not distinguishable'}")

        print("\nagreement between agents")
        for i, a in enumerate(present):
            for b in present[i + 1:]:
                ids = [x for x in key if x in got[a] and x in got[b]]
                same = sum(1 for x in ids if got[a][x] == got[b][x])
                print(f"  {a:8} vs {b:8}  identical verdict on {same}/{len(ids)}")

    print("\nSanity checks")
    for m, s in summary.items():
        flags = []
        if s["missing"]:
            flags.append(f"{len(s['missing'])} items missing")
        if s["accuracy"] > 0.97:
            flags.append("near-perfect — check for leakage before believing it")
        bias = abs(s["said_supported"] - s["n"] / 2) / s["n"]
        if bias > 0.25:
            flags.append(f"strong yes/no bias ({s['said_supported']}/{s['n']} supported)")
        print(f"  {m:8} {'; '.join(flags) if flags else 'nothing anomalous'}")

    out = os.path.join(DATA, "agent_results.json")
    json.dump({"harness": "claude-code-subagent", "n_items": len(key),
               "benchmark": "pairs.jsonl (shuffled labels)",
               "comparable_to_api_runs": False, "models": summary},
              open(out, "w", encoding="utf-8"), indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
