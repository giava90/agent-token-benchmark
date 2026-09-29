"""Assert the benchmark's integrity invariants. Free — no API calls.

Every check here exists because its absence cost us a result:

  1. One citation per claim. A sentence citing several works has no single right
     answer, and multi-citation sentences were 44% of positives but 67% of the
     items three or more configurations failed.
  2. Every candidate key resolvable in the bibliography on display. Drawing
     negatives from outside it made "key not found" a perfect predictor of the
     label - 20 of 117 items, all negatives.
  3. Claim ids derived from content, not from position. A positional counter
     renumbers whenever the filter changes, so the same id names a different
     sentence in each version and any join across versions is silently wrong.
  4. Labels not predictable from position. `i % 2 == 0` is balanced and readable
     off the index; an agent handed a slice of consecutive items scored 24/24.

Run this before every paid run. A leak that survives to publication costs more
than the run did.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))

# Under a fair shuffle the alternation rate is ~50%. Anything near 0 or 100 is a
# pattern. The bound is loose on purpose: it should catch structure, not noise.
ALTERNATION_BOUNDS = (0.25, 0.75)


def main() -> int:
    claims = corpus.read_jsonl(os.path.join(DATA, "claims.jsonl"))
    pairs = corpus.read_jsonl(os.path.join(DATA, "pairs.jsonl"))
    shown = {r["key"] for r in corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))}

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    print(f"claims {len(claims)}  pairs {len(pairs)}  bibliography shown {len(shown)} keys\n")

    # 1. one citation per claim
    multi = [c["claim_id"] for c in claims
             if c.get("n_keys", 1) > 1 or c.get("n_cite_sites", 1) > 1]
    check("every claim cites exactly one work", not multi,
          f"{len(multi)} offenders {multi[:3]}" if multi else "")

    bad_mask = [c["claim_id"] for c in claims if c["text"].count("[CITATION]") != 1]
    check("exactly one [CITATION] placeholder per claim", not bad_mask,
          f"{len(bad_mask)} offenders {bad_mask[:3]}" if bad_mask else "")

    # 2. every candidate resolvable, and resolvability uncorrelated with label
    unresolvable = [p for p in pairs if p["candidate_key"] not in shown]
    check("every candidate key is in the bibliography shown", not unresolvable,
          f"{len(unresolvable)} offenders" if unresolvable else "")

    # 3. ids are content-derived, so they survive a change of filter or section
    drifted = [c["claim_id"] for c in claims
               if not c["claim_id"].endswith(
                   hashlib.sha1(c["text_raw"].encode("utf-8")).hexdigest()[:8])]
    check("claim ids are derived from content, not position", not drifted,
          f"{len(drifted)} positional ids, e.g. {drifted[:3]}" if drifted else "")

    # 4. labels neither positional nor derivable from the gold set
    seq = [p["label"] == "supported" for p in sorted(pairs, key=lambda x: x["pair_id"])]
    alt = sum(1 for a, b in zip(seq, seq[1:]) if a != b) / max(1, len(seq) - 1)
    lo, hi = ALTERNATION_BOUNDS
    check("labels are not predictable from position", lo <= alt <= hi,
          f"alternation {alt:.0%} (fair shuffle ~50%, old positional leak 100%)")

    balance = sum(seq) / len(seq) if seq else 0
    check("classes are balanced", 0.4 <= balance <= 0.6, f"{balance:.0%} supported")

    mislabelled = [p["pair_id"] for p in pairs
                   if (p["candidate_key"] in p["gold_keys"]) != (p["label"] == "supported")]
    check("label agrees with the gold key set", not mislabelled,
          f"{len(mislabelled)} offenders {mislabelled[:3]}" if mislabelled else "")

    # A negative whose difficulty tier is systematically easier to spot than by
    # reading would reintroduce leak 2 in another form.
    tiers = {t: sum(1 for p in pairs if p["difficulty"] == t)
             for t in ("positive", "easy", "hard")}
    check("both negative tiers are populated", tiers["easy"] > 0 and tiers["hard"] > 0,
          json.dumps(tiers))

    print()
    if failures:
        print(f"{len(failures)} INVARIANT(S) VIOLATED - do not spend money on this benchmark.")
        return 1
    print("All invariants hold. Safe to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
