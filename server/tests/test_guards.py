"""Rate, daily-request and spend guards."""

from __future__ import annotations

import asyncio
import time

import pytest

from agentclinic.llm.guards import (
    BudgetExceeded,
    DailyCapExceeded,
    DailyRequestCounter,
    RunGuards,
    SpendTracker,
    TokenBucket,
)


async def test_token_bucket_paces_requests():
    bucket = TokenBucket(rate_per_minute=600, capacity=1)  # 10/s, so ~0.1s apart
    await bucket.acquire()
    started = time.monotonic()
    await bucket.acquire()
    await bucket.acquire()
    assert time.monotonic() - started >= 0.15


def test_daily_counter_is_per_utc_day_and_persists(tmp_path):
    path = tmp_path / "daily.json"
    c = DailyRequestCounter(path, limit=3)
    assert c.remaining() == 3
    c.record(); c.record()
    assert DailyRequestCounter(path, limit=3).remaining() == 1, "must survive a new process"


def test_daily_cap_raises_its_own_type(tmp_path):
    """A daily-cap breach aborts the run; a minute-cap breach does not."""
    c = DailyRequestCounter(tmp_path / "d.json", limit=1)
    c.record()
    with pytest.raises(DailyCapExceeded, match="resets at 00:00 UTC"):
        c.check()


def test_daily_counter_tolerates_a_corrupt_file(tmp_path):
    path = tmp_path / "d.json"
    path.write_text("{ not json")
    assert DailyRequestCounter(path, limit=5).remaining() == 5


def test_spend_cap_is_per_case_and_per_run():
    st = SpendTracker(per_case_cap=0.5, per_run_cap=1.0)
    st.add("a", 0.6)
    with pytest.raises(BudgetExceeded, match="case a"):
        st.check("a")
    st.check("b")           # a different case is unaffected
    st.add("b", 0.5)
    with pytest.raises(BudgetExceeded, match="run spend"):
        st.check("b")


def test_breach_carries_the_stop_reason_kind():
    st = SpendTracker(per_case_cap=0.0)
    st.add("a", 0.1)
    with pytest.raises(BudgetExceeded) as exc:
        st.check("a")
    assert exc.value.kind == "spend_cap"


async def test_run_guards_check_spend_before_spending_a_request(tmp_path):
    """Order matters: a doomed call must not consume daily allowance."""
    daily = DailyRequestCounter(tmp_path / "d.json", limit=10)
    spend = SpendTracker(per_case_cap=0.0)
    spend.add("a", 0.1)
    guards = RunGuards(bucket=TokenBucket(), daily=daily, spend=spend)
    with pytest.raises(BudgetExceeded):
        await guards.before_call("a")
    assert daily.used() == 0
