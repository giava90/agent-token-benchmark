"""Fill in missing abstracts from Crossref/arXiv. Free public APIs, no cost.

Results are cached in data/abstract_cache.json so re-runs cost no requests.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbbench import abstracts, corpus  # noqa: E402

DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
CACHE = os.path.join(DATA, "abstract_cache.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs", default=os.path.join(DATA, "refs_section.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    rows = corpus.read_jsonl(args.refs)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}

    todo = [r for r in rows if not r["abstract"] and r["key"] not in cache]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(rows)} refs, {sum(1 for r in rows if not r['abstract'])} without abstract, "
          f"{len(todo)} to fetch ({len(cache)} cached)")

    for i, r in enumerate(todo, 1):
        text, source = abstracts.fetch(r["doi"], r["title"], delay=args.delay)
        cache[r["key"]] = {"abstract": text, "source": source}
        status = f"{source} ({len(text)} chars)" if text else "NOT FOUND"
        print(f"  [{i}/{len(todo)}] {r['key'][:38]:38} {status}")
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(args.delay)

    filled = 0
    for r in rows:
        if not r["abstract"] and r["key"] in cache and cache[r["key"]]["abstract"]:
            r["abstract"] = cache[r["key"]]["abstract"]
            r["abstract_source"] = cache[r["key"]]["source"]
            filled += 1

    corpus.write_jsonl(args.refs, rows)
    have = sum(1 for r in rows if r["abstract"])
    print(f"\nfilled {filled}; coverage now {have}/{len(rows)}")
    still = [r["key"] for r in rows if not r["abstract"]]
    if still:
        print("still missing:", still)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
