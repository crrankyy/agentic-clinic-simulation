"""Building and running one encounter for the web app.

This mirrors what `cli.run` wires up, with three differences that matter:

* one case at a time, not an evaluation sweep;
* events are streamed as the graph produces them rather than read off the final
  state, because the point is to watch;
* the judge is never constructed here. The web app's reveal reads the stored
  ground truth directly (see `reveal.py`); judging is an evaluation concern and
  keeping it out of this path means no judge prompt, and so no ground truth,
  exists anywhere near the streaming code.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import load_budgets, load_models, load_test_costs
from ..data.loader import load_cases
from ..data.splits import select_eval_subset
from ..data.views import CaseStore
from ..graphs.schemas import make_orchestrator_decision
from ..graphs.state import new_state
from ..paths import DATASET_DIR, DATASET_FILE, RUNS_DIR
from ..tracing import Tracer
from .wire import to_wire

#: The web app serves only the cases the pipeline has actually been run on.
#: Anything else is a 404 — not a silent fallback to a different case.
SERVED_CASES = ("medqa-0002", "medqa-0009", "medqa-0012")

CONFIGS = ("single_doctor", "panel")

ENABLED_ACTIONS = frozenset({"ask_patient", "request_exam", "order_test"})


def served_case_ids() -> tuple[str, ...]:
    """The three cases, verified against the dev split rather than hardcoded.

    `SERVED_CASES` is the contract with the client; this asserts it still
    matches what `select_eval_subset` actually chooses, so the web app cannot
    quietly drift onto different cases than the runs it is compared against.
    """
    splits_path = DATASET_DIR / "splits.json"
    if not splits_path.exists():
        return SERVED_CASES
    cases = load_cases(DATASET_FILE)
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    chosen = tuple(select_eval_subset(cases, splits["dev"], n=3))
    if chosen != SERVED_CASES:
        raise RuntimeError(
            f"the dev subset is now {chosen} but the web app serves "
            f"{SERVED_CASES}; update SERVED_CASES deliberately."
        )
    return SERVED_CASES


@dataclass
class RunHandle:
    """A live encounter, and the buffer replaying it to late subscribers."""

    run_id: str
    case_id: str
    config: str
    started_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(
        timespec="seconds"))
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"          # running | finished | failed
    stop_reason: str | None = None
    final: dict[str, Any] | None = None
    error: str | None = None
    error_class: str | None = None
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    #: Woken on every append so subscribers need no polling interval.
    _tick: asyncio.Event = field(default_factory=asyncio.Event)

    def append(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)
        self._tick.set()

    def finish(self, *, status: str, stop_reason: str | None = None,
               final: dict[str, Any] | None = None, error: str | None = None,
               error_class: str | None = None) -> None:
        self.status, self.stop_reason, self.final, self.error = status, stop_reason, final, error
        self.error_class = error_class
        self._done.set()
        self._tick.set()


class Engine:
    """Owns the shared LLM client, guards and the live-run registry.

    One instance per process. The guards in particular *must* be shared: the
    OpenRouter rate limit and daily cap are per-account, so a per-run limiter
    would let two concurrent runs breach both.
    """

    def __init__(self) -> None:
        self.runs: dict[str, RunHandle] = {}
        self._cases = load_cases(DATASET_FILE)
        self.store = CaseStore(self._cases)
        self._models = load_models()
        self._costs = load_test_costs()
        self._shared_limits: Any = None
        from ..llm.preflight import CachedPreflight

        self.preflight = CachedPreflight(self._models)

    @property
    def models(self) -> Any:
        return self._models

    def case_ids(self) -> tuple[str, ...]:
        return served_case_ids()

    def objective(self, case_id: str) -> str:
        return self.store.doctor_view(case_id).objective_for_doctor

    def _caller(self, tracer: Tracer, timeout: float) -> Any:
        """A caller and guards for ONE run.

        The rate bucket and the daily counter are account-wide, so they are
        shared across runs. The spend tracker is not: shared, the $0.50
        per-case cap became a lifetime cap per case id, and a case would be
        refused after about a dozen web runs (M-35).
        """
        from ..llm.factory import build_caller, build_guards, build_shared_limits

        budgets = load_budgets()
        if self._shared_limits is None:
            self._shared_limits = build_shared_limits(self._models, budgets,
                                                      RUNS_DIR / ".daily.json")
        guards = build_guards(self._models, budgets, RUNS_DIR / ".daily.json",
                              shared=self._shared_limits)
        caller = build_caller(self._models, budgets, guards=guards, tracer=tracer)
        return caller, guards

    def build_graph(self, *, case_id: str, config: str, caller: Any, guards: Any,
                    max_turns: int) -> Any:
        from ..agents.gatekeeper import Gatekeeper, make_llm_disambiguator
        from ..agents.patient import Patient
        from ..graphs.encounter import build_encounter_graph
        from ..graphs.single_doctor import build_single_doctor_graph

        shared = dict(
            caller=caller,
            patient=Patient(self.store.patient_view(case_id), caller),
            gatekeeper=Gatekeeper(
                self.store.gatekeeper_view(case_id), self._costs,
                llm_disambiguate=make_llm_disambiguator(caller, case_id),
            ),
            case_id=case_id,
            decision_model=make_orchestrator_decision(ENABLED_ACTIONS),
            enabled=ENABLED_ACTIONS, max_turns=max_turns, guards=guards,
            question_overlap=load_budgets().question_repeat_overlap,
        )
        if config == "panel":
            return build_encounter_graph(costs=self._costs, **shared)
        return build_single_doctor_graph(**shared)

    def daily_remaining(self) -> int | None:
        """Free-tier requests left today, or None for a paid model (D-062)."""
        from ..llm.guards import DailyRequestCounter

        budgets = load_budgets()
        _, per_day = budgets.request_limits(self._models.is_free)
        return DailyRequestCounter(RUNS_DIR / ".daily.json", limit=per_day).remaining()

    def projected_requests(self, config: str, max_turns: int) -> int:
        """Same worst-case projection the CLI refuses on (cli.run)."""
        from ..llm.factory import projected_requests

        return projected_requests(config, max_turns)

    def start(self, *, case_id: str, config: str, max_turns: int) -> RunHandle:
        run_id = f"web-{config}-{uuid.uuid4().hex[:8]}"
        handle = RunHandle(run_id=run_id, case_id=case_id, config=config)
        self.runs[run_id] = handle
        asyncio.create_task(self._drive(handle, max_turns=max_turns))
        return handle

    async def _drive(self, handle: RunHandle, *, max_turns: int) -> None:
        run_dir = RUNS_DIR / handle.run_id
        tracer = Tracer(run_dir=run_dir)
        budgets = load_budgets(max_turns=max_turns, graph=handle.config)
        from ..eval.runmeta import write_run_meta

        write_run_meta(run_dir, config=handle.config, models=self._models,
                       max_turns=max_turns, cases=[handle.case_id])
        state: dict[str, Any] = {}
        status, error, error_class = "failed", None, None
        caller = None
        try:
            caller, guards = self._caller(tracer, budgets.timeout_seconds)
            graph = self.build_graph(case_id=handle.case_id, config=handle.config,
                                     caller=caller, guards=guards, max_turns=max_turns)
            view = self.store.doctor_view(handle.case_id)
            seen = 0

            async def consume() -> None:
                nonlocal state, seen
                async for chunk in graph.astream(
                    new_state(handle.case_id, view.objective_for_doctor),
                    {"recursion_limit": budgets.recursion_limit},
                    stream_mode="values",
                ):
                    state = chunk
                    log = chunk.get("encounter_log") or []
                    # `encounter_log` is append-only (an `operator.add` reducer),
                    # so everything past the high-water mark is new. Emitting a
                    # diff rather than the whole log keeps replay idempotent.
                    for i in range(seen, len(log)):
                        tracer.event(case_id=handle.case_id, event=log[i], seq=i)
                        handle.append(to_wire(log[i], seq=i))
                    seen = len(log)
                    await asyncio.sleep(0)

            # The CLI's per-case deadline, which the web path never had: a case
            # that cannot finish in this long is stuck, not slow.
            await asyncio.wait_for(consume(),
                                   timeout=budgets.max_turns * 6 * budgets.timeout_seconds)
            status = "finished"
        except Exception as exc:  # noqa: BLE001 — surfaced to the client as a status
            tracer.error(case_id=handle.case_id, node="graph", exc=exc)
            error_class = failure_class(exc)
            # Type and message only. An exception can carry prompt text, and a
            # traceback more so.
            error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            final = state.get("final")
            final_dump = final.model_dump() if final is not None else None
            if final_dump is not None:
                (run_dir / "finals.json").write_text(
                    json.dumps({handle.case_id: final_dump}, indent=2), encoding="utf-8")
            # Written on every exit, failures included (M-19): replay used to
            # report a crashed or dead-key run as "Finished", and a capped one
            # as finished voluntarily.
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "summary.json").write_text(json.dumps({
                "case_id": handle.case_id, "config": handle.config,
                "status": status, "stop_reason": state.get("stop_reason"),
                "error_class": error_class, "error": error,
                "spend_usd": (caller.guards.spend.case_total(handle.case_id)
                              if caller is not None and caller.guards is not None else None),
            }, indent=2), encoding="utf-8")
            handle.finish(status=status, stop_reason=state.get("stop_reason"),
                          final=final_dump, error=error, error_class=error_class)


def failure_class(exc: BaseException) -> str:
    """What the client should say about a failed run, by what happened."""
    from ..llm.guards import BudgetExceeded
    from ..llm.openrouter import ProviderAuthError, ProviderConfigError, ProviderError

    if isinstance(exc, ProviderAuthError):
        return "auth"
    if isinstance(exc, ProviderConfigError):
        return "config"
    if isinstance(exc, ProviderError):
        return exc.error_class
    if isinstance(exc, BudgetExceeded):
        return "budget"
    if isinstance(exc, asyncio.TimeoutError):
        return "deadline"
    return "crash"


async def stream_live(handle: RunHandle, *, start: int = 0) -> AsyncIterator[dict[str, Any]]:
    """Yield buffered events, then new ones as they land, then a terminal frame.

    Buffered-then-live is what lets a browser connect mid-run, or reconnect,
    without missing anything: `start` is the client's high-water mark.
    """
    i = start
    while True:
        while i < len(handle.events):
            yield {"type": "event", "data": handle.events[i]}
            i += 1
        if handle._done.is_set() and i >= len(handle.events):
            break
        handle._tick.clear()
        await asyncio.wait(
            [asyncio.create_task(handle._tick.wait()),
             asyncio.create_task(handle._done.wait())],
            return_when=asyncio.FIRST_COMPLETED, timeout=30,
        )
    yield {"type": "status", "data": {
        "status": handle.status, "stop_reason": handle.stop_reason,
        "final": handle.final, "error": handle.error, "error_class": handle.error_class,
        "run_id": handle.run_id, "case_id": handle.case_id, "config": handle.config,
    }}


def replay_events(run_dir: Path, case_id: str) -> list[dict[str, Any]]:
    """Read a completed run's transcript back out of its trace."""
    from .wire import from_trace

    trace = run_dir / "traces" / f"{case_id}.jsonl"
    if not trace.exists():
        return []
    out = []
    for line in trace.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "event":
            out.append(from_trace(record))
    return sorted(out, key=lambda e: e["seq"])
