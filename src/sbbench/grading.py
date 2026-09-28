"""Graders for the two tasks.

Both are exact and automatic. Parsing is deliberately strict-but-forgiving: an
unparseable reply is a failure, not a silent zero, so parser bugs cannot be
mistaken for model errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class VerifyResult:
    pair_id: str
    predicted: str | None      # "supported" | "not_supported" | None if unparseable
    truth: str
    correct: bool
    difficulty: str
    parse_failed: bool


def parse_verdict(text: str) -> str | None:
    if not text:
        return None
    head = text.strip().splitlines()[0].upper()
    head = re.sub(r"[^A-Z_]", " ", head)
    tokens = head.split()
    # Check NOT_SUPPORTED first: "SUPPORTED" is a substring of it.
    if "NOT_SUPPORTED" in tokens or ("NOT" in tokens and "SUPPORTED" in tokens):
        return "not_supported"
    if "SUPPORTED" in tokens:
        return "supported"
    return None


def grade_verify(pair: dict, reply: str) -> VerifyResult:
    pred = parse_verdict(reply)
    return VerifyResult(
        pair_id=pair["pair_id"],
        predicted=pred,
        truth=pair["label"],
        correct=(pred == pair["label"]),
        difficulty=pair["difficulty"],
        parse_failed=(pred is None),
    )


@dataclass
class AttributeResult:
    claim_id: str
    predicted: list[str]
    gold: list[str]
    exact_set: bool
    recall: float
    precision: float
    parse_failed: bool


_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_:.\-]{2,}")


def parse_keys(text: str) -> list[str]:
    if not text:
        return []
    line = text.strip().splitlines()[0]
    line = line.replace("[", " ").replace("]", " ")
    out: list[str] = []
    for tok in re.split(r"[,\s;]+", line):
        tok = tok.strip().strip(".")
        if tok and _KEY.fullmatch(tok):
            if tok not in out:
                out.append(tok)
    return out


def grade_attribute(claim: dict, reply: str) -> AttributeResult:
    pred = parse_keys(reply)
    gold = list(claim["gold_keys"])
    gset, pset = set(gold), set(pred)
    hit = len(gset & pset)
    return AttributeResult(
        claim_id=claim["claim_id"],
        predicted=pred,
        gold=gold,
        exact_set=(gset == pset),
        recall=hit / len(gset) if gset else 0.0,
        precision=hit / len(pset) if pset else 0.0,
        parse_failed=(not pred),
    )


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Wilson rather than normal-approximation: at n around 30 (the per-difficulty
    tiers) the normal interval misbehaves near 0 and 1, and can run outside
    [0, 1] entirely.
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarise_verify(results: list[VerifyResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}
    by_diff: dict[str, list[VerifyResult]] = {}
    for r in results:
        by_diff.setdefault(r.difficulty, []).append(r)
    hits = sum(r.correct for r in results)
    lo, hi = wilson_interval(hits, n)
    out = {
        "n": n,
        "accuracy": hits / n,
        "ci95_low": lo,
        "ci95_high": hi,
        "ci95_halfwidth": (hi - lo) / 2,
        "parse_failures": sum(r.parse_failed for r in results),
    }
    for diff, rs in sorted(by_diff.items()):
        h = sum(r.correct for r in rs)
        dlo, dhi = wilson_interval(h, len(rs))
        out[f"accuracy:{diff}"] = h / len(rs)
        out[f"n:{diff}"] = len(rs)
        out[f"ci95:{diff}"] = (dlo, dhi)
    return out


def summarise_attribute(results: list[AttributeResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}
    return {
        "n": n,
        "exact_set": sum(r.exact_set for r in results) / n,
        "recall": sum(r.recall for r in results) / n,
        "precision": sum(r.precision for r in results) / n,
        "parse_failures": sum(r.parse_failed for r in results),
    }
