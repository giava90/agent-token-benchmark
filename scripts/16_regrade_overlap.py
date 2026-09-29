"""Re-grade the paid run on the subset of items that survive into the corrected
benchmark unchanged. Free - no API calls, just a join on the run log.

A pair's label is DERIVED (candidate in gold_keys or not), so an item whose
(claim_id, candidate_key) is identical in v1 and v4 is literally the same item:
same prompt, same correct answer. Those replies are still valid evidence.
"""
import json, itertools
from math import comb

def load(p):
    return [json.loads(l) for l in open(p, encoding='utf-8')]

# NOTE: claim_id is a positional counter over KEPT claims, so it renumbers when
# the filter changes - the same id is a different sentence in v1 and v4. Join on
# the sentence text, which is stable.
v1 = {p['claim_text']: p for p in load('data/pairs_v1_alternating.jsonl')}
v4 = {p['claim_text']: p for p in load('data/pairs.jsonl')}

# items that survive unchanged: same sentence AND same candidate key drawn
overlap = {t for t, p in v4.items()
           if t in v1 and v1[t]['candidate_key'] == p['candidate_key']}
assert all(v1[t]['label'] == v4[t]['label'] for t in overlap)

id2text = {p['claim_id']: p['claim_text'] for p in load('data/pairs_v1_alternating.jsonl')}
rows = [r for r in load('runs/full_run.jsonl') if not r.get('_meta')]
byarm = {}
for r in rows:
    t = id2text.get(r['item_id'].rsplit('-p', 1)[0])
    if t:
        byarm.setdefault(r['arm'], {})[t] = r

print(f"v4 benchmark: {len(v4)} items")
print(f"of which unchanged from the paid v1 run: {len(overlap)}")
print(f"   positives {sum(1 for c in overlap if v4[c]['label']=='supported')}"
      f"  negatives {sum(1 for c in overlap if v4[c]['label']!='supported')}")
print()

pub = {'opus-low': .9487, 'opus-default': .8718, 'sonnet-default': .7949,
       'haiku': .7692, 'sonnet-low': .6838}
print(f"{'configuration':<16} {'published (117)':>16} {'same items, corrected':>23} {'shift':>8}")
acc = {}
for arm in ['opus-low', 'opus-default', 'sonnet-default', 'haiku', 'sonnet-low']:
    got = byarm[arm]
    hits = [c for c in overlap if c in got and got[c]['correct']]
    seen = [c for c in overlap if c in got]
    a = len(hits) / len(seen)
    acc[arm] = {c: (c in hits) for c in seen}
    print(f"{arm:<16} {pub[arm]*100:>15.1f}% {a*100:>22.1f}% {(a-pub[arm])*100:>+7.1f}pp")

def mcnemar(a, b):
    """exact two-sided sign test on discordant pairs"""
    shared = set(acc[a]) & set(acc[b])
    w = sum(1 for c in shared if acc[a][c] and not acc[b][c])
    l = sum(1 for c in shared if acc[b][c] and not acc[a][c])
    n = w + l
    if n == 0:
        return w, l, 1.0
    k = min(w, l)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)
    return w, l, p

print()
print("headline comparison, on the surviving items only")
w, l, p = mcnemar('opus-low', 'opus-default')
print(f"   opus-low vs opus-default: better on {w}, worse on {l}, exact p = {p:.4f}")
print(f"   (published on all 117: better on 9, worse on 0, p = 0.004)")

print()
print("easy-negative tier, the one the over-reasoning story rested on")
for arm in ['opus-low', 'opus-default']:
    e = [c for c in overlap if v4[c]['difficulty'] == 'easy' and c in acc[arm]]
    print(f"   {arm:<14} {sum(acc[arm][c] for c in e)}/{len(e)}")
