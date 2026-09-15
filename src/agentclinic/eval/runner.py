"""Runs encounters, judges them, and records per-case metrics.

Outcome mapping is the fiddly part and is deliberately explicit, because at n=3
each classification moves headline accuracy by 33 points:

* `crash`     — the graph raised. No final answer exists.
* `error`     — a harness failure produced the answer: budget exhausted, daily
                request cap, or structured output that never validated. These
                are excluded from accuracy **and** from coverage, because they
                are not the doctor declining to answer.
* `abstained` — the doctor genuinely declined. Excluded from accuracy, counted
                in coverage.
* `scored`    — a real answer, judged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from ..agents.judge import Judge, top_k
from ..data.models import Case
from ..data.views import CaseStore
from ..graphs.state import Event, new_state
from .metrics import Outcome

#: Stop reasons that mean the harness, not the doctor, ended the encounter.
HARNESS_STOPS = {"budget_exhausted", "request_cap", "parse_failure"}


@dataclass
class CaseResult:
    case_id: str
    outcome: Outcome
    stop_reason: str | None = None
    forced_stop: bool = False
    match_type: str | None = None
    judge_correct: bool = False
    lenient_correct: bool = False
    top_1: bool = False
    top_3: bool = False
    top_5: bool = False
    in_differential: bool = False
    diagnosis: str = ""
    final_confidence: float = 0.0
    abstained: bool = False
    turns: int = 0
    patient_questions: int = 0
    tests_ordered: int = 0
    exams_requested: int = 0
    unlisted_tests: int = 0
    match_tiers: dict[str, int] = field(default_factory=dict)
    patient_unknown_rate: float = 0.0
    red_flag_turn: int | None = None
    test_cost_usd: float = 0.0
    api_cost_usd: float = 0.0
    parse_failures: int = 0
    latency_s: float = 0.0
    dx_in_results: bool = False
    dx_tokens_in_results: bool = False
    error: str | None = None

    def row(self) -> dict[str, Any]:
        out = asdict(self)
        out["match_tiers"] = ";".join(f"{k}={v}" for k, v in sorted(self.match_tiers.items()))
        return out


def _derive(events: Sequence[Event]) -> dict[str, Any]:
    """Pull per-case counters out of the append-only log.

    `red_flag_turn` comes from here rather than from `state["red_flags"]`
    because that key is last-write: a flag raised on turn 3 and omitted from
    turn 4's update would vanish, and the metric would report the wrong turn.
    """
    tiers: dict[str, int] = {}
    answers = [e for e in events if e.kind == "answer"]
    for e in events:
        tier = e.meta.get("tier")
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
    red = next((e.turn for e in events if e.kind == "red_flag"), None)
    unknown = sum(1 for e in answers if e.meta.get("unknown") == "True")
    return {
        "patient_questions": sum(1 for e in events if e.kind == "question"),
        "tests_ordered": sum(1 for e in events if e.kind == "test" and e.actor == "doctor"),
        "exams_requested": sum(1 for e in events if e.kind == "exam" and e.actor == "doctor"),
        "unlisted_tests": sum(1 for e in events if e.kind == "unlisted_test"),
        "match_tiers": tiers,
        "red_flag_turn": red,
        "patient_unknown_rate": (unknown / len(answers)) if answers else 0.0,
    }


async def run_case(
    *,
    case: Case,
    store: CaseStore,
    build_graph: Callable[[str], Any],
    judge: Judge | None,
    recursion_limit: int,
    tracer: Any = None,
    guards: Any = None,
    caller: Any = None,
) -> CaseResult:
    """Run one encounter and judge it. Never raises — a crash becomes a result."""
    started = time.monotonic()
    result = CaseResult(
        case_id=case.case_id, outcome="crash",
        dx_in_results=case.dx_in_results, dx_tokens_in_results=case.dx_tokens_in_results,
    )
    try:
        graph = build_graph(case.case_id)
        view = store.doctor_view(case.case_id)
        state = await graph.ainvoke(
            new_state(case.case_id, view.objective_for_doctor),
            {"recursion_limit": recursion_limit},
        )
    except Exception as exc:  # noqa: BLE001 — a crash is a recorded outcome
        result.error = f"{type(exc).__name__}: {exc}"[:400]
        result.latency_s = time.monotonic() - started
        if tracer is not None:
            # Type and message only; a traceback can carry prompt text.
            tracer.error(case_id=case.case_id, node="graph", exc=exc)
        return result

    events = state.get("encounter_log", [])
    final = state.get("final")
    result.stop_reason = state.get("stop_reason")
    result.forced_stop = result.stop_reason not in (None, "finalize")
    result.turns = int(state.get("turn", 0))
    result.test_cost_usd = float(state.get("test_cost_usd", 0.0))
    # D-039: the client owns the authoritative total; state holds a snapshot
    # that lags by at least one node, so read the tracker when we have it.
    result.api_cost_usd = (guards.spend.case_total(case.case_id) if guards
                           else float(state.get("spend_usd", 0.0)))
    # State counts only repair *exhaustions*; the caller counts every repair.
    result.parse_failures = max(
        int(state.get("parse_failures", 0)),
        caller.parse_failures_for(case.case_id) if caller else 0,
    )
    for key, value in _derive(events).items():
        setattr(result, key, value)

    if final is None:
        result.outcome = "crash"
        result.error = "graph finished without a final answer"
    elif result.stop_reason in HARNESS_STOPS:
        result.outcome = "error"
        result.abstained = bool(final.abstain)
        result.diagnosis = final.diagnosis
        result.final_confidence = float(final.confidence)
    elif final.abstain:
        result.outcome = "abstained"
        result.abstained = True
        result.final_confidence = float(final.confidence)
    else:
        result.outcome = "scored"
        result.diagnosis = final.diagnosis
        result.final_confidence = float(final.confidence)
        if judge is not None:
            try:
                verdict = await judge.verdict(
                    case_id=case.case_id, final=final, view=store.judge_view(case.case_id)
                )
            except Exception as exc:  # noqa: BLE001 — a judge failure is a result
                # Contained per case. A judge outage must not destroy a run that
                # has already spent its whole OpenRouter budget on the encounters.
                # The message may echo the judge prompt, which contains ground
                # truth, so it goes to judge.jsonl and NOT to results.csv.
                result.outcome = "error"
                result.error = f"judge failed: {type(exc).__name__}"
                if tracer is not None:
                    tracer.judge(case_id=case.case_id, event="judge_error",
                                 error_type=type(exc).__name__, error_message=str(exc)[:500])
            else:
                result.match_type = verdict.match_type
                result.judge_correct = verdict.correct
                result.lenient_correct = verdict.lenient_correct
                result.top_1 = top_k(verdict, 1)
                result.top_3 = top_k(verdict, 3)
                result.top_5 = top_k(verdict, 5)
                result.in_differential = any(verdict.entry_matches)

    result.latency_s = time.monotonic() - started
    return result


async def run_evaluation(
    *,
    cases: Sequence[Case],
    store: CaseStore,
    build_graph: Callable[[str], Any],
    judge: Judge | None,
    recursion_limit: int,
    concurrency: int = 4,
    tracer: Any = None,
    guards: Any = None,
    caller: Any = None,
) -> list[CaseResult]:
    """Run every case, bounded by a semaphore. Results come back in case order."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> CaseResult:
        async with semaphore:
            return await run_case(case=case, store=store, build_graph=build_graph,
                                  judge=judge, recursion_limit=recursion_limit,
                                  tracer=tracer, guards=guards, caller=caller)

    # return_exceptions keeps one pathological case from cancelling its siblings;
    # run_case already contains its own failures, so this is belt and braces.
    gathered = await asyncio.gather(*(one(c) for c in cases), return_exceptions=True)
    results = []
    for case, item in zip(cases, gathered):
        if isinstance(item, BaseException):
            results.append(CaseResult(case_id=case.case_id, outcome="crash",
                                      error=f"{type(item).__name__}: {item}"[:400],
                                      dx_in_results=case.dx_in_results,
                                      dx_tokens_in_results=case.dx_tokens_in_results))
        else:
            results.append(item)
    return sorted(results, key=lambda r: r.case_id)


def write_results_csv(results: Sequence[CaseResult], path: Path) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r.row() for r in results]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
