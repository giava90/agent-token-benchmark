"""Analyse the full run: accuracy with intervals, paired tests, cost breakdown.

Configurations saw identical items, so comparisons are paired and McNemar's
exact test is the right instrument - comparing two independent confidence
intervals would throw away the pairing and understate the evidence.
"""

import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import counting, grading, pricing, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

ORDER = ["opus-default", "opus-low", "sonnet-default", "sonnet-low", "haiku"]


def binom_two_sided(b: int, c: int) -> float:
    """Exact McNemar: P(|X - n/2| >= |b - n/2|) for X ~ Bin(n, 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> int:
    rows = runlog.load(os.path.join(RUNS, "full_run.jsonl"))
    by_arm = defaultdict(dict)
    for r in rows:
        by_arm[r["arm"]][r["item_id"]] = r

    arms = [a for a in ORDER if a in by_arm]
    results = {}

    print(f"{'configuration':16} {'n':>4} {'acc':>7} {'95% CI':>16} {'cost $':>8} "
          f"{'$/correct':>10} {'parse-fail':>11}")
    for a in arms:
        rs = list(by_arm[a].values())
        hits = sum(1 for r in rs if r["correct"])
        lo, hi = grading.wilson_interval(hits, len(rs))
        cost = sum(r["cost_usd"] for r in rs)
        pf = sum(1 for r in rs if r["parse_failed"])
        results[a] = {
            "n": len(rs), "correct": hits, "accuracy": hits / len(rs),
            "ci95": [lo, hi], "cost_usd": cost,
            "usd_per_correct": cost / hits if hits else None,
            "parse_failures": pf,
            "model": rs[0]["model"], "effort": rs[0]["effort"],
        }
        print(f"{a:16} {len(rs):4} {hits/len(rs):7.3f} "
              f"[{lo:.3f},{hi:.3f}] {cost:8.3f} {cost/hits:10.4f} {pf:11}")

    # ---- per difficulty ----------------------------------------------------
    print(f"\n{'configuration':16} {'positive':>18} {'easy neg':>18} {'hard neg':>18}")
    for a in arms:
        rs = list(by_arm[a].values())
        cells = []
        for d in ("positive", "easy", "hard"):
            sub = [r for r in rs if r["difficulty"] == d]
            h = sum(1 for r in sub if r["correct"])
            lo, hi = grading.wilson_interval(h, len(sub))
            cells.append(f"{h/len(sub):.2f} [{lo:.2f},{hi:.2f}]")
            results[a][f"acc_{d}"] = h / len(sub)
            results[a][f"n_{d}"] = len(sub)
        print(f"{a:16} {cells[0]:>18} {cells[1]:>18} {cells[2]:>18}")

    # ---- paired comparisons ------------------------------------------------
    print("\npaired comparisons (McNemar exact, same 117 items)")
    print(f"{'A vs B':34} {'A only':>7} {'B only':>7} {'p':>8}  verdict")
    pairs = [("opus-low", "opus-default"), ("sonnet-default", "sonnet-low"),
             ("opus-low", "sonnet-default"), ("opus-default", "haiku"),
             ("sonnet-default", "haiku"), ("opus-low", "haiku")]
    comparisons = []
    for a, b in pairs:
        if a not in by_arm or b not in by_arm:
            continue
        items = set(by_arm[a]) & set(by_arm[b])
        b_only = sum(1 for i in items if by_arm[a][i]["correct"] and not by_arm[b][i]["correct"])
        c_only = sum(1 for i in items if not by_arm[a][i]["correct"] and by_arm[b][i]["correct"])
        p = binom_two_sided(b_only, c_only)
        verdict = "significant" if p < 0.05 else "not distinguishable"
        print(f"{a + ' vs ' + b:34} {b_only:7} {c_only:7} {p:8.4f}  {verdict}")
        comparisons.append({"a": a, "b": b, "a_only": b_only, "b_only": c_only,
                            "p": p, "significant": p < 0.05})

    # ---- multiple comparisons ---------------------------------------------
    # Six paired tests on one dataset. Holm-Bonferroni controls the family-wise
    # error rate without assuming independence.
    print("\nHolm-Bonferroni correction across the 6 comparisons")
    ordered = sorted(comparisons, key=lambda c: c["p"])
    m = len(ordered)
    prev = 0.0
    for i, c in enumerate(ordered):
        adj = min(1.0, max(prev, (m - i) * c["p"]))
        prev = adj
        c["p_holm"] = adj
        c["survives_correction"] = adj < 0.05
        print(f"  {c['a'] + ' vs ' + c['b']:34} p={c['p']:.4f} -> p_holm={adj:.4f}  "
              f"{'survives' if adj < 0.05 else 'DOES NOT survive'}")

    # ---- error types -------------------------------------------------------
    # Accepting a bad citation and rejecting a good one are not equally costly
    # for a citation checker. Report them separately rather than folding both
    # into one accuracy figure.
    print(f"\n{'configuration':16} {'false accepts':>14} {'false rejects':>14} {'FA rate':>9} {'FR rate':>9}")
    for a in arms:
        rs = list(by_arm[a].values())
        neg = [r for r in rs if r["truth"] == "not_supported"]
        pos = [r for r in rs if r["truth"] == "supported"]
        fa = sum(1 for r in neg if not r["correct"])   # bad citation waved through
        fr = sum(1 for r in pos if not r["correct"])   # good citation rejected
        results[a].update({
            "false_accepts": fa, "false_rejects": fr,
            "false_accept_rate": fa / len(neg), "false_reject_rate": fr / len(pos),
        })
        print(f"{a:16} {fa:14} {fr:14} {fa/len(neg):9.3f} {pos and fr/len(pos):9.3f}")

    print("\ncost per bad citation caught (false accepts weighted 5x false rejects)")
    for a in sorted(arms, key=lambda x: results[x]["cost_usd"] /
                    max(1, results[x]["n"] - 5 * results[x]["false_accepts"] - results[x]["false_rejects"])):
        r = results[a]
        weighted_right = r["n"] - 5 * r["false_accepts"] - r["false_rejects"]
        r["weighted_score"] = weighted_right / r["n"]
        r["usd_per_weighted"] = r["cost_usd"] / weighted_right if weighted_right > 0 else None
        val = f"${r['usd_per_weighted']:.4f}" if r["usd_per_weighted"] else "no net value"
        print(f"  {a:16} weighted score {weighted_right:6.0f}/{r['n']}  {val}")

    # ---- where the money went ---------------------------------------------
    print(f"\n{'configuration':16} {'cache read':>11} {'input':>8} {'thinking':>9} {'answer':>8}")
    for a in arms:
        rs = list(by_arm[a].values())
        model = rs[0]["model"]
        pr = pricing.get(model)
        n = len(rs)
        read_t = sum(r["cache_read_input_tokens"] for r in rs) / n
        in_t = sum(r["input_tokens"] for r in rs) / n
        out_t = sum(r["output_tokens"] for r in rs) / n
        bd_path = os.path.join(DATA, f"breakdown_{model}_{rs[0]['effort'] or 'default'}.json")
        if os.path.exists(bd_path):
            bd = json.load(open(bd_path, encoding="utf-8"))
            share = bd["mean_answer_tokens"] / bd["mean_output_tokens"] if bd["mean_output_tokens"] else 0.5
        else:
            share = 0.5
        ans_t, think_t = out_t * share, out_t * (1 - share)
        comp = {
            "cache_read": read_t * pr.cache_read / 1e6,
            "input": in_t * pr.input / 1e6,
            "thinking": think_t * pr.output / 1e6,
            "answer": ans_t * pr.output / 1e6,
        }
        tot = sum(comp.values()) or 1
        results[a]["cost_components_usd_per_item"] = comp
        results[a]["cost_component_share"] = {k: v / tot for k, v in comp.items()}
        print(f"{a:16} {100*comp['cache_read']/tot:10.1f}% {100*comp['input']/tot:7.1f}% "
              f"{100*comp['thinking']/tot:8.1f}% {100*comp['answer']/tot:7.1f}%")

    total = sum(results[a]["cost_usd"] for a in arms)
    out = {"configurations": results, "comparisons": comparisons,
           "total_cost_usd": total, "n_items": results[arms[0]]["n"]}
    path = os.path.join(DATA, "results.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
    print(f"\ntotal spent: ${total:.3f}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
