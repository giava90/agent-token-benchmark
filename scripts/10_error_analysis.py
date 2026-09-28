"""Which items do the models get wrong, and do they agree about it?

An item every configuration fails is not a model failure - it is a property of
the item. Two kinds are interesting to the review's author:

  * a POSITIVE that everything rejects  -> the citation may be weak
  * a HARD NEGATIVE that everything accepts -> the swapped reference may
    genuinely support the claim, i.e. it was a defensible citation too

Both are findings about the corpus, not about the models.
"""

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, runlog  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RUNS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))
ORDER = ["opus-low", "opus-default", "sonnet-default", "haiku", "sonnet-low"]


def main() -> int:
    rows = runlog.load(os.path.join(RUNS, "full_run.jsonl"))
    pairs = {p["pair_id"]: p for p in corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))}
    refs = {r["key"]: r for r in corpus.read_jsonl(os.path.join(DATA, "refs_all.jsonl"))}

    by_item = defaultdict(dict)
    for r in rows:
        by_item[r["item_id"]][r["arm"]] = r["correct"]
    arms = [a for a in ORDER if any(a in v for v in by_item.values())]

    # how many configurations failed each item
    fails = {i: sum(1 for a in arms if v.get(a) is False) for i, v in by_item.items()}
    dist = Counter(fails.values())
    print("agreement on failure")
    print(f"{'configs wrong':>14} {'items':>6}")
    for k in range(len(arms) + 1):
        print(f"{k:>14} {dist.get(k, 0):>6}")
    n_all = dist.get(len(arms), 0)
    print(f"\n{n_all} of {len(by_item)} items were failed by ALL {len(arms)} configurations.")
    print("If errors were independent, that count would be near zero - so these")
    print("items are hard in themselves, not victims of one model's quirk.\n")

    # pairwise error overlap
    print("pairwise error overlap (items both got wrong / items either got wrong)")
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            both = sum(1 for v in by_item.values() if v.get(a) is False and v.get(b) is False)
            either = sum(1 for v in by_item.values() if v.get(a) is False or v.get(b) is False)
            print(f"  {a:16} vs {b:16} {both:3}/{either:3}  jaccard {both/either:.2f}")

    # ---- if errors are near-independent, voting should beat any single arm ---
    print("\n" + "=" * 72)
    print("MAJORITY VOTE - does low error overlap pay?")
    print("=" * 72)
    verdicts = defaultdict(dict)
    costs = defaultdict(float)
    truth = {}
    for r in rows:
        verdicts[r["item_id"]][r["arm"]] = r["predicted"]
        costs[r["arm"]] += r["cost_usd"]
        truth[r["item_id"]] = r["truth"]

    def vote_accuracy(members):
        hits = 0
        for item, t in truth.items():
            votes = [verdicts[item].get(m) for m in members]
            votes = [v for v in votes if v]
            if not votes:
                continue
            win = Counter(votes).most_common(1)[0][0]
            hits += (win == t)
        return hits / len(truth)

    ensembles = [
        ("haiku + sonnet-default + sonnet-low", ["haiku", "sonnet-default", "sonnet-low"]),
        ("haiku + sonnet-default + opus-low", ["haiku", "sonnet-default", "opus-low"]),
        ("haiku + sonnet-default + opus-default", ["haiku", "sonnet-default", "opus-default"]),
        ("all five", arms),
    ]
    print(f"{'ensemble':40} {'accuracy':>9} {'cost':>8}  vs best single")
    best_single = max(arms, key=lambda a: sum(1 for i in truth if verdicts[i].get(a) == truth[i]))
    best_acc = sum(1 for i in truth if verdicts[i].get(best_single) == truth[i]) / len(truth)
    for name, members in ensembles:
        acc = vote_accuracy(members)
        c = sum(costs[m] for m in members)
        delta = acc - best_acc
        print(f"{name:40} {acc:9.3f} {c:8.3f}  {delta:+.3f}")
    print(f"\nbest single arm: {best_single} at {best_acc:.3f} for ${costs[best_single]:.3f}")

    # the universally-failed items, split by what kind of error they are
    THRESH = max(3, len(arms) - 2)
    universal = [i for i, c in fails.items() if c >= THRESH]
    print(f"\n(listing items failed by {THRESH} or more of {len(arms)} configurations)")
    flagged = {"weak_citation": [], "defensible_alternative": []}

    print(f"\n{'=' * 72}\nITEMS EVERY CONFIGURATION GOT WRONG\n{'=' * 72}")
    for pid in sorted(universal):
        p = pairs[pid]
        cand = refs.get(p["candidate_key"], {})
        kind = ("weak_citation" if p["label"] == "supported" else "defensible_alternative")
        flagged[kind].append({
            "pair_id": pid, "claim": p["claim_text"], "label": p["label"],
            "difficulty": p["difficulty"], "candidate_key": p["candidate_key"],
            "candidate_title": cand.get("title", ""), "gold_keys": p["gold_keys"],
        })
        print(f"\n--- {pid}   truth={p['label']}   tier={p['difficulty']}")
        print(f"CLAIM     {p['claim'][:300] if 'claim' in p else p['claim_text'][:300]}")
        print(f"CANDIDATE [{p['candidate_key']}] {cand.get('title', '')[:90]}")
        if p["label"] == "supported":
            print("READING   every model rejected a reference the review cites here")
        else:
            print(f"READING   every model accepted a reference the review does NOT cite here")
            print(f"          (review cites: {', '.join(p['gold_keys'])})")

    print(f"\n{'=' * 72}\nSUMMARY FOR THE AUTHOR\n{'=' * 72}")
    print(f"{len(flagged['weak_citation'])} citations the review makes that every model "
          f"judged unsupported.")
    print(f"{len(flagged['defensible_alternative'])} references the review does not cite "
          f"that every model judged supportive.")
    print("\nNeither is proof of an error. Both are worth a human look, and that is")
    print("the practical output of a citation-grounding run: a short worklist,")
    print("ranked by how many independent models agree.")

    out = os.path.join(DATA, "error_analysis.json")
    json.dump({"n_arms": len(arms), "arms": arms,
               "failure_agreement": {str(k): v for k, v in sorted(dist.items())},
               "universally_failed": len(universal), "flagged": flagged},
              open(out, "w", encoding="utf-8"), indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
