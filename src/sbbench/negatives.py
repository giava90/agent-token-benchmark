"""Build the verification benchmark: claim + candidate reference -> supported?

Negatives are injected at two difficulty levels so the study has a hard tail:

  easy  - a reference drawn from elsewhere in the bibliography, usually a
          different topic. Cheap to reject.
  hard  - a reference cited elsewhere in the SAME subsection. Topically adjacent
          and genuinely confusable; this is where architectures separate.

Determinism matters more than elegance here: the same seed must reproduce the
same benchmark, or runs are not comparable across architectures.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, asdict


@dataclass
class Pair:
    pair_id: str
    claim_id: str
    claim_text: str
    candidate_key: str
    label: str            # "supported" | "not_supported"
    difficulty: str       # "positive" | "easy" | "hard"
    gold_keys: list[str]
    subsection: str
    subsubsection: str

    def to_dict(self) -> dict:
        return asdict(self)


def _subsection_pool(claims: list[dict]) -> dict[str, set[str]]:
    """Keys cited within each subsubsection (falling back to subsection)."""
    pool: dict[str, set[str]] = {}
    for c in claims:
        bucket = c.get("subsubsection") or c.get("subsection") or c.get("section") or ""
        pool.setdefault(bucket, set()).update(c["gold_keys"])
    return pool


def build_pairs(
    claims: list[dict],
    all_keys: list[str],
    seed: int = 20260928,
    hard_fraction: float = 0.5,
) -> list[Pair]:
    """One pair per claim, alternating positive and negative.

    Half the claims yield a positive (a real gold key), half a negative. Among
    negatives, `hard_fraction` are drawn from the same subsection.
    """
    rng = random.Random(seed)
    pool = _subsection_pool(claims)
    universe = sorted(all_keys)
    pairs: list[Pair] = []

    ordered = sorted(claims, key=lambda c: c["claim_id"])
    for i, c in enumerate(ordered):
        gold = set(c["gold_keys"])
        bucket = c.get("subsubsection") or c.get("subsection") or c.get("section") or ""

        make_positive = (i % 2 == 0)
        if make_positive:
            candidate = rng.choice(sorted(gold))
            label, difficulty = "supported", "positive"
        else:
            want_hard = rng.random() < hard_fraction
            near = sorted(pool.get(bucket, set()) - gold)
            far = [k for k in universe if k not in gold]
            if want_hard and near:
                candidate, difficulty = rng.choice(near), "hard"
            elif far:
                candidate, difficulty = rng.choice(far), "easy"
            else:
                continue
            label = "not_supported"

        pairs.append(
            Pair(
                pair_id=f"{c['claim_id']}-p",
                claim_id=c["claim_id"],
                claim_text=c["text"],
                candidate_key=candidate,
                label=label,
                difficulty=difficulty,
                gold_keys=c["gold_keys"],
                subsection=c.get("subsection", ""),
                subsubsection=c.get("subsubsection", ""),
            )
        )
    return pairs


def summarise(pairs: list[Pair]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pairs:
        out[p.label] = out.get(p.label, 0) + 1
        out[f"difficulty:{p.difficulty}"] = out.get(f"difficulty:{p.difficulty}", 0) + 1
    return out
