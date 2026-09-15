"""Append-only JSONL tracing, one file per case.

Brief §7: every LLM call, tool call and node transition is recorded with
timestamps, node, inputs, outputs, tokens, latency and cost.

One rule is load-bearing rather than cosmetic: **judge records never enter a
per-case trace file**. The judge is the only component that sees ground truth,
and an exception from it can carry its prompt. Per-case artefacts record an
exception's type and message, never a traceback.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Tracer:
    """Writes `runs/<run_id>/traces/<case_id>.jsonl` and `judge.jsonl`."""

    run_dir: Path
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        (self.run_dir / "traces").mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps({"ts": _now(), **record}, ensure_ascii=False, default=str)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _case_path(self, case_id: str) -> Path:
        return self.run_dir / "traces" / f"{case_id}.jsonl"

    # --- encounter-side ---------------------------------------------------
    def node(self, *, case_id: str, node: str, event: str, **fields: Any) -> None:
        self._write(self._case_path(case_id), {"kind": "node", "node": node, "event": event, **fields})

    def llm_call(
        self, *, case_id: str, node: str, prompt_tokens: int, completion_tokens: int,
        cost: float | None, latency_s: float, **fields: Any,
    ) -> None:
        self._write(self._case_path(case_id), {
            "kind": "llm_call", "node": node,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost": cost, "latency_s": round(latency_s, 3), **fields,
        })

    def event(self, *, case_id: str, event: Any, seq: int) -> None:
        """Persist one encounter-transcript entry.

        Until now events lived only in graph state and their aggregates only in
        `results.csv`, so a crash in report generation lost the behaviour
        telemetry outright and there was nothing to replay a run from. `seq` is
        the index in `encounter_log`, which makes the stream replayable from an
        arbitrary offset and makes duplicate writes detectable.

        This is doctor-visible transcript content. It carries no ground truth —
        the same events the doctor-side graph already holds — but it is written
        to the per-case trace, never to `judge.jsonl`.
        """
        self._write(self._case_path(case_id), {
            "kind": "event", "seq": seq, "turn": event.turn,
            "event_kind": event.kind, "actor": event.actor,
            "text": event.text, "meta": dict(event.meta),
        })

    def tool_call(self, *, case_id: str, node: str, tool: str, **fields: Any) -> None:
        self._write(self._case_path(case_id), {"kind": "tool_call", "node": node, "tool": tool, **fields})

    def error(self, *, case_id: str, node: str, exc: BaseException) -> None:
        """Type and message only — never a traceback (PLAN.md §3.3 item 8)."""
        self._write(self._case_path(case_id), {
            "kind": "error", "node": node,
            "error_type": type(exc).__name__, "error_message": str(exc)[:500],
        })

    # --- judge-side, deliberately separate --------------------------------
    def judge(self, *, case_id: str, **fields: Any) -> None:
        self._write(self.run_dir / "judge.jsonl", {"kind": "judge", "case_id": case_id, **fields})
