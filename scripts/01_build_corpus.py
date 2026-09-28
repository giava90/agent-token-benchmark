"""Extract claims + reference index from the review. No API calls, no cost."""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import corpus  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="Measurement")
    ap.add_argument("--prefix", default="meas")
    ap.add_argument("--out", default=DATA)
    args = ap.parse_args()

    tex_path, bib = corpus.load_sources()
    claims = corpus.extract_claims(tex_path, args.section, args.prefix)

    cited: set[str] = set()
    for c in claims:
        cited.update(c.gold_keys)

    missing = sorted(k for k in cited if k not in bib)
    refs_section = corpus.reference_index(bib, cited)
    refs_all = corpus.reference_index(bib)

    corpus.write_jsonl(os.path.join(args.out, "claims.jsonl"), [c.to_dict() for c in claims])
    corpus.write_jsonl(os.path.join(args.out, "refs_section.jsonl"), refs_section)
    corpus.write_jsonl(os.path.join(args.out, "refs_all.jsonl"), refs_all)

    with_abs = sum(1 for r in refs_section if r["abstract"])
    multi = sum(1 for c in claims if c.n_keys > 1)

    print(f"section              : {args.section}")
    print(f"claims               : {len(claims)}")
    print(f"  single-key         : {len(claims) - multi}")
    print(f"  multi-key          : {multi}")
    print(f"unique cited keys    : {len(cited)}")
    print(f"  resolved in bib    : {len(cited) - len(missing)}")
    print(f"  MISSING from bib   : {len(missing)} {missing[:5]}")
    print(f"  with abstract      : {with_abs}/{len(refs_section)}")
    print(f"bib entries (all)    : {len(refs_all)}")
    print()
    print("wrote claims.jsonl, refs_section.jsonl, refs_all.jsonl to", os.path.normpath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
