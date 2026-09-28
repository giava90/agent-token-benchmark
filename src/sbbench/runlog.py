"""Per-request run log.

One JSONL row per API call. Everything the study reports is derived from these
rows, so they record raw usage fields rather than pre-computed summaries - a
wrong price constant can then be corrected without re-running anything.

Token counts are diagnostics only. The comparison metric is USD per completed
task, because tokenizers differ across models and token counts are therefore
not comparable between them.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import dataclass, asdict, field


@dataclass
class RunRecord:
    run_id: str
    arm: str                  # Z, A, B1, B2, C-naive, C-staggered, D
    task: str                 # "verify" | "attribute"
    model: str
    item_id: str              # pair_id or claim_id
    role: str = "worker"      # "orchestrator" | "worker" | "solo" | "keepalive"

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    turns: int = 1

    effort: str | None = None
    ttl: str = "5m"
    batch: bool = False
    cached: bool = True

    correct: bool | None = None
    predicted: str | None = None
    truth: str | None = None
    difficulty: str | None = None
    parse_failed: bool = False

    attempt: int = 1          # arm D re-runs failures at higher effort
    mock: bool = True
    ts: float = field(default_factory=time.time)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RunLog:
    def __init__(self, path: str, run_id: str, meta: dict | None = None):
        self.path = path
        self.run_id = run_id
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")
        self._write({
            "_meta": True,
            "run_id": run_id,
            "started": time.time(),
            "python": platform.python_version(),
            **(meta or {}),
        })

    def _write(self, obj: dict) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fh.flush()

    def add(self, rec: RunRecord) -> None:
        self._write(rec.to_dict())

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            if not obj.get("_meta"):
                rows.append(obj)
    return rows
