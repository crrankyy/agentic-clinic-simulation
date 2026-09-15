"""Rate, request and spend guards shared by every OpenRouter call in a run.

Three separate limits, because three separate things can go wrong:

* **Rate** — free models allow 20 requests/minute. The bucket runs at 18 to keep
  headroom. The limit is per *account*, not per connection, so a single shared
  bucket is the only thing that can enforce it; concurrency alone cannot.
* **Daily requests** — 1000/day with credits purchased. At 18/min that is
  exhausted in under an hour, so it is a live constraint, not a theoretical one.
  The counter is persisted, because the cap resets on a wall-clock day and a
  fresh process must not forget what an earlier one spent.
* **Spend** — the client owns the authoritative running total (D-039). State
  holds only a snapshot; a state-held total necessarily lags by at least one
  node, which would let the cap be exceeded silently.

A breach raises, and the graph converts that into a clean finalize rather than
an abort — see PLAN.md §5 item 5.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class NonRetryable(Exception):
    """Signals a condition the repair loop must not swallow.

    The loop deliberately catches broadly — a provider can return HTTP 200 with
    `choices: null`, and narrow excepts would let one hiccup cost a case. But
    some exceptions mean "stop", not "try again": a budget breach (retrying
    spends money that is gone) and a test script running out (retrying turns
    "the graph looped further than expected" into a passing test).
    """


class BudgetExceeded(NonRetryable, RuntimeError):
    """A spend, request or daily cap was hit. Carries which, for `stop_reason`."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind  # "spend_cap" | "request_cap"


class DailyCapExceeded(BudgetExceeded):
    """The provider's daily allowance is gone. Aborts the run, not just the case."""

    def __init__(self, detail: str) -> None:
        super().__init__("request_cap", detail)


@dataclass
class TokenBucket:
    """Async token bucket. Shared by every worker; refills continuously."""

    rate_per_minute: float = 18.0
    capacity: float = 18.0
    _tokens: float = field(default=18.0, init=False)
    _updated: float = field(default_factory=time.monotonic, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._updated) * self.rate_per_minute / 60.0,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) * 60.0 / self.rate_per_minute)


class DailyRequestCounter:
    """Persistent UTC-dated request counter.

    Stored as a tiny JSON file so the count survives process restarts; a run
    resumed the next morning correctly sees a fresh allowance.
    """

    def __init__(self, path: Path, limit: int = 1000) -> None:
        self.path = path
        self.limit = limit

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def used(self) -> int:
        return int(self._read().get(self._today(), 0))

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def record(self, n: int = 1) -> None:
        data = self._read()
        today = self._today()
        data[today] = int(data.get(today, 0)) + n
        # Keep only the last few days so the file cannot grow unbounded.
        for key in sorted(data)[:-7]:
            data.pop(key, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")

    def check(self) -> None:
        if self.remaining() <= 0:
            raise DailyCapExceeded(
                f"daily request allowance of {self.limit} is exhausted "
                f"({self.used()} used today). The cap resets at 00:00 UTC."
            )


class SpendTracker:
    """Authoritative running cost total for a run (D-039)."""

    def __init__(self, per_case_cap: float = 0.50, per_run_cap: float = 25.0) -> None:
        self.per_case_cap = per_case_cap
        self.per_run_cap = per_run_cap
        self.run_total = 0.0
        self._case_totals: dict[str, float] = {}

    def case_total(self, case_id: str) -> float:
        return self._case_totals.get(case_id, 0.0)

    def add(self, case_id: str, cost: float) -> None:
        self.run_total += cost
        self._case_totals[case_id] = self.case_total(case_id) + cost

    def check(self, case_id: str) -> None:
        """Raise before a call that the budget cannot afford."""
        if self.run_total >= self.per_run_cap:
            raise BudgetExceeded("spend_cap", f"run spend ${self.run_total:.4f} >= ${self.per_run_cap}")
        if self.case_total(case_id) >= self.per_case_cap:
            raise BudgetExceeded(
                "spend_cap",
                f"case {case_id} spend ${self.case_total(case_id):.4f} >= ${self.per_case_cap}",
            )


@dataclass
class RunGuards:
    """Everything that must be checked before an OpenRouter call."""

    bucket: TokenBucket
    daily: DailyRequestCounter
    spend: SpendTracker

    async def before_call(self, case_id: str) -> None:
        self.spend.check(case_id)
        self.daily.check()
        await self.bucket.acquire()
        self.daily.record()
