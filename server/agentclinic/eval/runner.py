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
from ..llm.guards import DailyCapExceeded
from ..llm.openrouter import ProviderAuthError, ProviderConfigError
from .metrics import Outcome

#: Stop reasons that mean the harness, not the doctor, ended the encounter.
#: provider_error (D-059): the provider was unreachable after every retry --
#: a property of the harness and the day, not of the doctor.
HARNESS_STOPS = {"budget_exhausted", "request_cap", "parse_failure", "provider_error"}


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
    #: D-056 / M-41: the redundancy the glaring mistake was made of, measured.
    #: `guard_blocks` are proposals rejected before they ran (no turn spent);
    #: `repeat_orders` are orders that still reached the gatekeeper and got a
    #: record already delivered; `no_yield_actions` are executed actions that
    #: produced nothing new (refused, repeated, or the patient did not know).
    guard_blocks: int = 0
    repeat_orders: int = 0
    no_yield_actions: int = 0
    #: Executed actions after the leading diagnosis last changed.
    actions_after_leader_settled: int | None = None
    transient_failures: int = 0
    #: The ordered differential, kept so the judge can be re-run later without
    #: re-running the encounter. `results.csv` gets the names; `finals.json`
    #: keeps the whole answer.
    differential: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        out = asdict(self)
        out["match_tiers"] = ";".join(f"{k}={v}" for k, v in sorted(self.match_tiers.items()))
        out["differential"] = " | ".join(self.differential)
        return out


def result_from_row(row: dict[str, Any]) -> CaseResult:
    """Rebuild a CaseResult from a results.csv row or a `row()` dict, every field.

    The judge command used to copy a hand-maintained list of fields, so every
    field added later was silently dropped to its default in a re-judged report
    -- "actions that produced nothing new: 0" for a run that had one. Coercion
    follows each field's declared type, so a new field needs no edit here.
    """
    import dataclasses
    import typing

    hints = typing.get_type_hints(CaseResult)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(CaseResult):
        if f.name not in row:
            continue
        raw = row[f.name]
        kind = hints[f.name]
        text = "" if raw is None else raw
        if f.name == "match_tiers":
            if isinstance(raw, dict):
                kwargs[f.name] = {k: int(v) for k, v in raw.items()}
            else:
                kwargs[f.name] = {k: int(v) for k, v in
                                  (x.split("=") for x in str(text).split(";") if "=" in x)}
        elif f.name == "differential":
            kwargs[f.name] = (list(raw) if isinstance(raw, list)
                              else [x.strip() for x in str(text).split("|") if x.strip()])
        elif kind is bool:
            kwargs[f.name] = raw if isinstance(raw, bool) else str(text).strip().lower() in {"true", "1", "yes"}
        elif kind is int:
            kwargs[f.name] = int(float(text or 0))
        elif kind is float:
            kwargs[f.name] = float(text or 0)
        elif str(text) == "" and type(None) in typing.get_args(kind):
            kwargs[f.name] = None
        elif int in typing.get_args(kind):
            kwargs[f.name] = int(float(text))
        else:
            kwargs[f.name] = raw
    return CaseResult(**kwargs)


def _result(case: Case, outcome: Outcome, error: str | None = None) -> CaseResult:
    return CaseResult(case_id=case.case_id, outcome=outcome,
                      error=error[:400] if error else None,
                      dx_in_results=case.dx_in_results,
                      dx_tokens_in_results=case.dx_tokens_in_results)


def apply_verdict(result: CaseResult, final: Any, verdict: Any) -> None:
    """Record a judged answer: the model's verdict or a hand-assigned one."""
    result.outcome, result.error = "scored", None
    result.diagnosis = final.diagnosis
    result.differential = [d.diagnosis for d in final.differential]
    result.match_type = verdict.match_type
    result.judge_correct = verdict.correct
    result.lenient_correct = verdict.lenient_correct
    result.top_1, result.top_3, result.top_5 = (top_k(verdict, k) for k in (1, 3, 5))
    result.in_differential = any(verdict.entry_matches)


async def drive(graph: Any, initial: Any, *, recursion_limit: int,
                on_event: Callable[[int, Event], Any], state: dict[str, Any]) -> None:
    """Stream an encounter, calling `on_event` once per new transcript entry.

    `encounter_log` is append-only (an `operator.add` reducer), so everything
    past the high-water mark is new. `state` is updated in place, so a caller
    keeps the last state even when this raises -- with `ainvoke` a crashed or
    timed-out case kept no transcript and no counters at all (M-38).
    """
    seen = 0
    async for chunk in graph.astream(initial, {"recursion_limit": recursion_limit},
                                     stream_mode="values"):
        state.clear()
        state.update(chunk)
        log = chunk.get("encounter_log") or []
        for seq in range(seen, len(log)):
            on_event(seq, log[seq])
        seen = len(log)
        await asyncio.sleep(0)   # let stream subscribers run between chunks


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
    from ..graphs.ledger import build_ledger, same_leader

    ledger = build_ledger(events)
    leaders = [(e.turn, e.text.removeprefix("leading: ")) for e in events
               if e.kind == "hypothesis" and e.actor == "doctor" and e.text.startswith("leading: ")]
    settled_turn = None
    for i in range(len(leaders)):
        if all(same_leader(leaders[i][1], later) for _, later in leaders[i:]):
            settled_turn = leaders[i][0]
            break
    return {
        "patient_questions": sum(1 for e in events if e.kind == "question"),
        "tests_ordered": sum(1 for e in events if e.kind == "test" and e.actor == "doctor"),
        "exams_requested": sum(1 for e in events if e.kind == "exam" and e.actor == "doctor"),
        "unlisted_tests": sum(1 for e in events if e.kind == "unlisted_test"),
        "match_tiers": tiers,
        "red_flag_turn": red,
        "patient_unknown_rate": (unknown / len(answers)) if answers else 0.0,
        "guard_blocks": sum(1 for e in events if e.kind == "guard"),
        "repeat_orders": sum(1 for x in ledger if x.outcome == "repeat"),
        "no_yield_actions": sum(1 for x in ledger
                                if x.outcome in ("not_in_case", "repeat", "could_not_answer")),
        "actions_after_leader_settled": (sum(1 for x in ledger if x.turn > settled_turn)
                                         if settled_turn is not None else None),
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
    finals: dict[str, Any] | None = None,
    case_deadline_s: float | None = None,
) -> CaseResult:
    """Run one encounter and judge it. Never raises — a crash becomes a result.

    `case_deadline_s` is defence in depth behind `LLMCaller.call_timeout`. The
    turn cap bounds *turns*, not wall-clock: a provider that trickles bytes can
    hold a call open indefinitely, and 20 turns of that is unbounded. One hung
    case must not hold up a run.
    """
    started = time.monotonic()
    result = _result(case, "crash")
    state: dict[str, Any] = {}

    def on_event(seq: int, event: Event) -> None:
        if tracer is not None:
            tracer.event(case_id=case.case_id, event=event, seq=seq)

    try:
        graph = build_graph(case.case_id)
        view = store.doctor_view(case.case_id)
        coro = drive(graph, new_state(case.case_id, view.objective_for_doctor),
                     recursion_limit=recursion_limit, on_event=on_event, state=state)
        await (asyncio.wait_for(coro, timeout=case_deadline_s) if case_deadline_s else coro)
    except (ProviderAuthError, ProviderConfigError, DailyCapExceeded):
        # Run-fatal: every later case would fail the same way. The evaluation
        # aborts instead of crashing each case in turn (lesson 36).
        raise
    except Exception as exc:  # noqa: BLE001 — a crash is a recorded outcome
        result.error = f"{type(exc).__name__}: {exc}"[:400]
        result.latency_s = time.monotonic() - started
        if tracer is not None:
            # Type and message only; a traceback can carry prompt text.
            tracer.error(case_id=case.case_id, node="graph", exc=exc)
        # What happened before the crash still counts.
        for key, value in _derive(state.get("encounter_log", [])).items():
            setattr(result, key, value)
        result.turns = int(state.get("turn", 0) or 0)
        return result

    events = state.get("encounter_log", [])
    final = state.get("final")
    if final is not None and finals is not None:
        finals[case.case_id] = final.model_dump()
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
    if tracer is not None:
        # What the transcript alone cannot say, so a report rebuilt from the
        # trace (D-050) is exact rather than inferred.
        tracer.node(case_id=case.case_id, node="runner", event="case_end",
                    stop_reason=result.stop_reason, turns=result.turns,
                    test_cost_usd=result.test_cost_usd, api_cost_usd=result.api_cost_usd)
    result.transient_failures = (caller.transient_failures_for(case.case_id)
                                 if caller is not None and hasattr(caller, "transient_failures_for")
                                 else 0)
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
        result.differential = [d.diagnosis for d in final.differential]
    elif final.abstain:
        result.outcome = "abstained"
        result.abstained = True
        result.final_confidence = float(final.confidence)
    else:
        result.outcome = "scored"
        result.diagnosis = final.diagnosis
        result.final_confidence = float(final.confidence)
        result.differential = [d.diagnosis for d in final.differential]
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
                apply_verdict(result, final, verdict)

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
    finals: dict[str, Any] | None = None,
    case_deadline_s: float | None = None,
) -> list[CaseResult]:
    """Run every case, bounded by a semaphore. Results come back in case order."""
    semaphore = asyncio.Semaphore(concurrency)
    fatal: list[BaseException] = []

    async def one(case: Case) -> CaseResult:
        async with semaphore:
            if fatal:
                # A dead key or an unserved model fails every case the same
                # way; record why instead of spending a request to find out.
                return _result(case, "error", f"aborted: {type(fatal[0]).__name__}")
            try:
                return await run_case(case=case, store=store, build_graph=build_graph,
                                      judge=judge, recursion_limit=recursion_limit,
                                      tracer=tracer, guards=guards, caller=caller,
                                      finals=finals, case_deadline_s=case_deadline_s)
            except (ProviderAuthError, ProviderConfigError, DailyCapExceeded) as exc:
                fatal.append(exc)
                return _result(case, "error", f"{type(exc).__name__}: {exc}")

    # run_case contains every other failure itself, so nothing reaches gather.
    results = await asyncio.gather(*(one(c) for c in cases))
    return sorted(results, key=lambda r: r.case_id)


def write_finals(finals: dict[str, Any], path: Path) -> None:
    """Persist the full final answers.

    Judging is the only step that needs an Anthropic credential, and encounters
    are by far the expensive half — ~180 OpenRouter requests against a 1000/day
    allowance. Keeping the answers means a missing credential costs three judge
    calls to recover from, not a whole re-run.
    """
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(finals, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")


def write_results_csv(results: Sequence[CaseResult], path: Path) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r.row() for r in results]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def rebuild_results(run_dir: Path, cases_by_id: dict[str, Any]) -> list[CaseResult]:
    """Reconstruct per-case results from `finals.json` plus the traces.

    For when a run's encounters completed but `results.csv` was never written —
    a crash in report generation, an interrupted process. The encounters are the
    expensive half (hundreds of requests); the bookkeeping is derivable, so
    losing the CSV should not mean re-running them.

    The trace carries the full transcript (D-054) and a `case_end` record per
    finished case, so the counters come back exact. A run recorded before
    events were persisted cannot be rebuilt, and says so rather than printing
    defaults as though they were measured zeros.
    """
    import json

    finals = json.loads((run_dir / "finals.json").read_text(encoding="utf-8"))
    out: list[CaseResult] = []
    for case_id, answer in finals.items():
        case = cases_by_id.get(case_id)
        result = CaseResult(
            case_id=case_id, outcome="scored",
            diagnosis=answer.get("diagnosis", ""),
            final_confidence=float(answer.get("confidence", 0.0)),
            abstained=bool(answer.get("abstain")),
            differential=[d["diagnosis"] for d in answer.get("differential", [])],
            dx_in_results=bool(case.dx_in_results) if case else False,
            dx_tokens_in_results=bool(case.dx_tokens_in_results) if case else False,
        )
        if result.abstained:
            result.outcome = "abstained"

        trace = run_dir / "traces" / f"{case_id}.jsonl"
        if trace.exists():
            records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            calls = [r for r in records if r.get("kind") == "llm_call"]
            result.latency_s = sum(r.get("latency_s", 0.0) for r in calls)
            result.api_cost_usd = sum(r.get("cost") or 0.0 for r in calls)
            result.parse_failures = sum(1 for r in records if r.get("event") == "parse_failure")
            events = [Event(turn=r["turn"], kind=r["event_kind"], actor=r["actor"],
                            text=r["text"], meta=r.get("meta") or {})
                      for r in records if r.get("kind") == "event"]
            if not events:
                raise ValueError(f"{trace} was recorded before events were persisted "
                                 "(D-054); its counters cannot be rebuilt")
            for key, value in _derive(events).items():
                setattr(result, key, value)
            # A crashed case has no `case_end`; its stop reason stays None.
            end = next((r for r in records if r.get("event") == "case_end"), {})
            result.stop_reason = end.get("stop_reason")
            result.turns = int(end.get("turns") or max(e.turn for e in events))
            result.test_cost_usd = float(end.get("test_cost_usd") or 0.0)
            result.forced_stop = result.stop_reason not in (None, "finalize")
            result.transient_failures = sum(
                1 for r in calls
                if r.get("status") == "failed" and r.get("error_class") not in (None, "content"))
        out.append(result)
    return sorted(out, key=lambda r: r.case_id)
