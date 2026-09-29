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
    ap.add_argument("--allow-multi-cite", action="store_true",
                    help="keep sentences citing several works; they are not "
                         "cleanly gradable, so the default drops them")
    args = ap.parse_args()

    tex_path, bib = corpus.load_sources()
    claims = corpus.extract_claims(tex_path, args.section, args.prefix,
                                   single_cite_only=not args.allow_multi_cite)

    # The bibliography on display is every key the section cites, including the
    # keys of sentences the single-citation filter dropped. The filter is about
    # whether a claim can be graded, not about what the model may look up - and
    # a bigger shown bibliography is both a better cache prefix and a richer
    # pool to draw negatives from.
    shown = corpus.extract_claims(tex_path, args.section, args.prefix,
                                  single_cite_only=False)
    cited: set[str] = set()
    for c in shown:
        cited.update(c.gold_keys)

    missing = sorted(k for k in cited if k not in bib)
    refs_section = corpus.reference_index(bib, cited)
    refs_all = corpus.reference_index(bib)

    corpus.write_jsonl(os.path.join(args.out, "claims.jsonl"), [c.to_dict() for c in claims])
    corpus.write_jsonl(os.path.join(args.out, "refs_section.jsonl"), refs_section)
    corpus.write_jsonl(os.path.join(args.out, "refs_all.jsonl"), refs_all)

    with_abs = sum(1 for r in refs_section if r["abstract"])
    multi = sum(1 for c in claims if c.n_keys > 1 or c.n_cite_sites > 1)

    print(f"section              : {args.section}")
    print(f"claims               : {len(claims)}")
    print(f"  single-citation    : {len(claims) - multi}")
    print(f"  multi-citation     : {multi}"
          f"{'  (kept: --allow-multi-cite)' if multi else '  (dropped)'}")
    print(f"claims shown as bib  : {len(shown)} sentences -> {len(cited)} keys")
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
