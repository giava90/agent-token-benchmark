"""The experiment arms, written against the mock client.

Each arm is a different way of spending tokens on the same 117 items. The
signature is identical so the runner can swap them, and so the real client can
later be dropped in behind the same calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pricing, prompts
from .grading import grade_verify
from .mockclient import MockAnthropic
from .runlog import RunRecord

# Simulated wall time for one request, and the keep-alive cadence.
SECONDS_PER_CALL = 8.0
IDLE_GAP_SECONDS = 1800.0     # 30 min idle: past the 5-minute TTL, past 1h? no
KEEPALIVE_EVERY = 240.0       # just under the 5-minute TTL


@dataclass
class ArmConfig:
    name: str
    model: str
    ttl: str = "5m"
    batch: bool = False
    cached: bool = True
    effort: str | None = None


def _record(cfg: ArmConfig, pair: dict, resp, role: str, attempt: int = 1) -> RunRecord:
    g = grade_verify(pair, resp.text)
    u = resp.usage.to_dict()
    return RunRecord(
        run_id="",
        arm=cfg.name,
        task="verify",
        model=cfg.model,
        item_id=pair["pair_id"],
        role=role,
        cost_usd=pricing.cost_from_usage(cfg.model, u, ttl=cfg.ttl, batch=cfg.batch),
        wall_seconds=SECONDS_PER_CALL,
        effort=cfg.effort,
        ttl=cfg.ttl,
        batch=cfg.batch,
        cached=cfg.cached,
        correct=g.correct,
        predicted=g.predicted,
        truth=g.truth,
        difficulty=g.difficulty,
        parse_failed=g.parse_failed,
        attempt=attempt,
        **u,
    )


def _ask(client: MockAnthropic, cfg: ArmConfig, prefix: str, pair: dict, ref: dict,
         visible: bool = True):
    # The prefix is always sent. `use_cache` decides whether it is cached or
    # billed at full input rate on every request - not whether it exists.
    return client.create(
        model=cfg.model,
        cached_prefix=prefix,
        tail=prompts.verify_tail(pair["claim_text"], ref),
        ttl=cfg.ttl,
        use_cache=cfg.cached,
        answer=client.verdict(pair["pair_id"], pair["label"]),
        item_id=pair["pair_id"],
        visible_immediately=visible,
    )


def run_sequential(client: MockAnthropic, cfg: ArmConfig, prefix: str,
                   pairs: list[dict], refs: dict[str, dict]) -> list[RunRecord]:
    """Z / baseline arms: one request per item, cache warm throughout."""
    out = []
    for p in pairs:
        resp = _ask(client, cfg, prefix, p, refs[p["candidate_key"]])
        out.append(_record(cfg, p, resp, role="solo"))
        client.advance(SECONDS_PER_CALL)
    return out


def run_idle(client: MockAnthropic, cfg: ArmConfig, prefix: str,
             pairs: list[dict], refs: dict[str, dict],
             idle_after: int = 20, gap: float = IDLE_GAP_SECONDS) -> list[RunRecord]:
    """Arm A: work, go idle past the TTL, come back and pay the cold write again."""
    out = []
    for i, p in enumerate(pairs):
        if i == idle_after:
            client.advance(gap)
        resp = _ask(client, cfg, prefix, p, refs[p["candidate_key"]])
        out.append(_record(cfg, p, resp, role="solo"))
        client.advance(SECONDS_PER_CALL)
    return out


def run_keepalive(client: MockAnthropic, cfg: ArmConfig, prefix: str,
                  pairs: list[dict], refs: dict[str, dict],
                  idle_after: int = 20, gap: float = IDLE_GAP_SECONDS) -> list[RunRecord]:
    """Arm B2: same idle gap, bridged by max_tokens:0 pings.

    The ping bills a cache read and zero output tokens, and a read refreshes the
    entry's timer for free.
    """
    out = []
    for i, p in enumerate(pairs):
        if i == idle_after:
            elapsed = 0.0
            while elapsed < gap:
                client.advance(KEEPALIVE_EVERY)
                elapsed += KEEPALIVE_EVERY
                ping = client.create(
                    model=cfg.model, cached_prefix=prefix, tail="warm",
                    ttl=cfg.ttl, use_cache=cfg.cached, answer="", output_tokens=0,
                )
                u = ping.usage.to_dict()
                out.append(RunRecord(
                    run_id="", arm=cfg.name, task="verify", model=cfg.model,
                    item_id=f"keepalive-{int(elapsed)}", role="keepalive",
                    cost_usd=pricing.cost_from_usage(cfg.model, u, ttl=cfg.ttl, batch=cfg.batch),
                    wall_seconds=0.5, ttl=cfg.ttl, batch=cfg.batch, cached=cfg.cached,
                    notes="max_tokens=0 pre-warm", **u,
                ))
        resp = _ask(client, cfg, prefix, p, refs[p["candidate_key"]])
        out.append(_record(cfg, p, resp, role="solo"))
        client.advance(SECONDS_PER_CALL)
    return out


def run_fanout(client: MockAnthropic, cfg: ArmConfig, prefix: str,
               pairs: list[dict], refs: dict[str, dict],
               lanes: int = 8, staggered: bool = False) -> list[RunRecord]:
    """Arm C: parallel workers.

    Naive: every lane in a wave fires before any response starts, so none can
    read what the others are writing - N writes, zero reads.
    Staggered: send one, wait for it to begin responding, then fan out.
    """
    out = []
    first = True
    for start in range(0, len(pairs), lanes):
        wave = pairs[start : start + lanes]
        if staggered and first and wave:
            lead = wave[0]
            resp = _ask(client, cfg, prefix, lead, refs[lead["candidate_key"]], visible=True)
            out.append(_record(cfg, lead, resp, role="worker"))
            wave = wave[1:]
            first = False
        for p in wave:
            resp = _ask(client, cfg, prefix, p, refs[p["candidate_key"]],
                        visible=staggered)
            out.append(_record(cfg, p, resp, role="worker"))
        if not staggered:
            client.reveal_pending()
        client.advance(SECONDS_PER_CALL)
        first = False
    return out
