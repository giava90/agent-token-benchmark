"""Build the verification pairs (positives + injected negatives). No cost."""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus, negatives  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--hard-fraction", type=float, default=0.5)
    args = ap.parse_args()

    claims = corpus.read_jsonl(os.path.join(DATA, "claims.jsonl"))
    # Draw negatives ONLY from the bibliography the model is shown. Using the
    # full 393-entry file here makes unresolvable keys a perfect tell for
    # "not supported" - see the docstring in negatives.build_pairs.
    refs = corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))
    all_keys = [r["key"] for r in refs]

    bad = [c["claim_id"] for c in claims
           if c.get("n_keys", 1) > 1 or c.get("n_cite_sites", 1) > 1]
    if bad:
        raise SystemExit(
            f"{len(bad)} claims cite more than one work, e.g. {bad[:3]}. "
            "A pair built on one is not cleanly gradable - rebuild claims.jsonl "
            "with scripts/01_build_corpus.py (single-citation is the default)."
        )

    pairs = negatives.build_pairs(claims, all_keys, seed=args.seed,
                                  hard_fraction=args.hard_fraction)
    corpus.write_jsonl(os.path.join(DATA, "pairs.jsonl"), [p.to_dict() for p in pairs])

    s = negatives.summarise(pairs)
    print(f"pairs: {len(pairs)}  (seed {args.seed})")
    for k in sorted(s):
        print(f"  {k:28} {s[k]}")

    section_keys = {r["key"] for r in corpus.read_jsonl(os.path.join(DATA, "refs_section.jsonl"))}
    outside = sum(1 for p in pairs if p.candidate_key not in section_keys)
    print(f"\ncandidates drawn from outside the section's own keys: {outside}")
    print("wrote pairs.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
