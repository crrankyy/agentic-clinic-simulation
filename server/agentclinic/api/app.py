"""FastAPI surface for watching an encounter unfold.

Educational simulation only. Never medical advice, never real patient data.

The route layout encodes the isolation rule rather than documenting it: every
transcript route is built from doctor-visible state, and ground truth exists on
exactly one route (`/reveal`) which refuses to answer while an encounter is
still running. There is no code path on which the two share a payload.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import load_budgets, load_dotenv
from ..eval.runmeta import read_json, read_run_meta
from ..llm.factory import projected_requests
from ..paths import CLIENT_DIR, RUNS_DIR
from .engine import CONFIGS, Engine, replay_events, stream_live

DISCLAIMER = (
    "Educational simulation only. Not medical advice. This is a simulation over "
    "public benchmark data and says nothing about clinical performance. It must "
    "never be presented or used as medical advice, and must never process real "
    "patient data."
)

app = FastAPI(title="AgentClinic — encounter viewer", version="0.1.0")

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        load_dotenv()
        _engine = Engine()
    return _engine


class StartRun(BaseModel):
    case_id: str
    config: str = "panel"
    max_turns: int = Field(default=20, ge=1, le=20)


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    e = engine()
    models = e.models
    budgets = load_budgets()
    check = await e.preflight.get()
    _, per_day = budgets.request_limits(models.is_free)
    return {
        "disclaimer": DISCLAIMER,
        "model": models.model,
        "provider_pin": models.provider.pin,
        "configs": list(CONFIGS),
        # D-062: a request allowance only exists on the free tier.
        "tier": "free" if models.is_free else "paid",
        "daily_remaining": e.daily_remaining(),
        "daily_limit": per_day,
        "spend_cap_per_case_usd": budgets.spend_per_case_usd,
        # M-17: whether the key works, checked without spending a token.
        "credential": {"ok": check.ok, "reason": check.reason,
                       "key_limit_remaining": check.key_limit_remaining},
    }


def _case_stop(run_dir: Any, case_id: str, summary: dict[str, Any]) -> str | None:
    """A web run's summary.json, else the `case_end` record the CLI runner writes."""
    if summary.get("case_id") == case_id:
        return summary.get("stop_reason")
    trace = run_dir / "traces" / f"{case_id}.jsonl"
    for line in trace.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("event") == "case_end":
            return record.get("stop_reason")
    return None


def _status(summary: dict[str, Any], finals: dict[str, Any], case_id: str) -> str:
    # A web run with no summary.json never reached its finally block: the
    # process died mid-run. It is not "finished" (M-19).
    return summary.get("status") or ("finished" if case_id in finals else "incomplete")


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}")


def _run_dir(run_id: str) -> Any:
    """A stored run's directory, or 404. Never a path outside runs/.

    Replay and reveal now accept any recorded case, so the ids that end up in
    file paths are validated rather than trusted.
    """
    if not _RUN_ID.fullmatch(run_id) or run_id in (".", ".."):
        raise HTTPException(404, f"no run {run_id!r}")
    d = RUNS_DIR / run_id
    if not d.is_dir() or d.resolve().parent != RUNS_DIR.resolve():
        raise HTTPException(404, f"no run {run_id!r}")
    return d


def _recorded(run_dir: Any, case_id: str | None) -> bool:
    """A known case with a transcript in this run -- what replay may show."""
    return bool(case_id) and case_id in engine().store.case_ids() and (
        run_dir / "traces" / f"{case_id}.jsonl").is_file()


@app.get("/api/cases")
def cases() -> list[dict[str, Any]]:
    """The three served cases. Objective only — this is the doctor's view."""
    e = engine()
    return [{"case_id": cid, "objective": e.objective(cid)} for cid in e.case_ids()]


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    """Live runs from this process, plus completed runs on disk for replay."""
    e = engine()
    out: list[dict[str, Any]] = [
        {"run_id": h.run_id, "case_id": h.case_id, "config": h.config,
         "model": e.models.model, "started": h.started_utc,
         "status": h.status, "source": "live", "events": len(h.events)}
        for h in e.runs.values()
    ]
    known = set(e.store.case_ids())
    if RUNS_DIR.is_dir():
        for d in sorted(RUNS_DIR.iterdir(), reverse=True):
            if not d.is_dir() or d.name in e.runs:
                continue
            traces = d / "traces"
            if not traces.is_dir():
                continue
            for trace in sorted(traces.glob("*.jsonl")):
                case_id = trace.stem
                # Any recorded case replays -- a 10-case run shows all ten.
                # Starting a live encounter is still limited to the served set.
                if case_id not in known:
                    continue
                n = len(replay_events(d, case_id))
                if not n:
                    continue  # recorded before events were persisted
                summary, meta = read_json(d / "summary.json"), read_run_meta(d)
                finals = read_json(d / "finals.json")
                out.append({"run_id": d.name, "case_id": case_id,
                            "config": meta.get("config"), "model": meta.get("model"),
                            "provider_pin": meta.get("provider_pin"),
                            "max_turns": meta.get("max_turns"),
                            "status": _status(summary, finals, case_id),
                            "stop_reason": _case_stop(d, case_id, summary),
                            "started": meta.get("started_utc", ""),
                            "source": "replay", "events": n})
    # Latest run first; cases within a run in case order.
    out.sort(key=lambda r: r["case_id"])
    out.sort(key=lambda r: (r.get("started") or "", r["run_id"]), reverse=True)
    return out


@app.post("/api/runs")
async def start_run(body: StartRun) -> dict[str, Any]:
    # `async` is load-bearing, not style: a sync endpoint runs in FastAPI's
    # threadpool, where `asyncio.create_task` raises "no running event loop" and
    # the encounter never starts.
    e = engine()
    if body.case_id not in e.case_ids():
        raise HTTPException(404, f"case {body.case_id!r} is not served; "
                                 f"this app serves {list(e.case_ids())}")
    if body.config not in CONFIGS:
        raise HTTPException(400, f"config must be one of {list(CONFIGS)}")

    # M-17: a dead key used to be discovered 0.9 s into the run, after three
    # attempts reported as "did not validate". Check it first, for free.
    check = await e.preflight.get(force=True)
    if not check.ok:
        raise HTTPException(503, f"cannot start: {check.reason}")

    # The same pre-flight the CLI refuses on. Discovering the daily cap mid-run
    # turns a watchable encounter into a wall of 429s. Free tier only (D-062).
    projected = projected_requests(body.config, body.max_turns)
    remaining = e.daily_remaining()
    if remaining is not None and projected > remaining:
        raise HTTPException(429, f"this run needs roughly {projected} requests but "
                                 f"only {remaining} remain in today's allowance")
    handle = e.start(case_id=body.case_id, config=body.config, max_turns=body.max_turns)
    return {"run_id": handle.run_id, "case_id": handle.case_id,
            "config": handle.config, "projected_requests": projected}


def _sse(payload: dict[str, Any]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload['data'])}\n\n"


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, start: int = Query(0, ge=0),
                 case_id: str | None = None) -> StreamingResponse:
    """Server-sent events. Identical payloads whether live or replayed."""
    e = engine()

    if run_id in e.runs:
        handle = e.runs[run_id]

        async def live() -> AsyncIterator[str]:
            async for payload in stream_live(handle, start=start):
                yield _sse(payload)
        gen: AsyncIterator[str] = live()
    else:
        run_dir = _run_dir(run_id)
        if not _recorded(run_dir, case_id):
            raise HTTPException(404, "replay needs a ?case_id= recorded in this run")
        events = replay_events(run_dir, case_id)
        if not events:
            raise HTTPException(404, "that run has no persisted transcript "
                                     "(recorded before events were saved)")
        finals = read_json(run_dir / "finals.json")
        summary, meta = read_json(run_dir / "summary.json"), read_run_meta(run_dir)
        stop_reason = _case_stop(run_dir, case_id, summary)

        async def replay() -> AsyncIterator[str]:
            for ev in events[start:]:
                yield _sse({"type": "event", "data": ev})
            # The recorded outcome, not an assumed one (M-19).
            yield _sse({"type": "status", "data": {
                "status": _status(summary, finals, case_id),
                "stop_reason": stop_reason, "final": finals.get(case_id),
                "model": meta.get("model"),
                "error": summary.get("error"), "error_class": summary.get("error_class"),
                "run_id": run_id, "case_id": case_id, "config": meta.get("config")}})
        gen = replay()

    return StreamingResponse(gen, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/runs/{run_id}/reveal")
def reveal(run_id: str, case_id: str | None = None) -> dict[str, Any]:
    """Ground truth — and the only route that has any.

    Refuses while the encounter is running. The transcript stream cannot carry
    this, and this cannot carry a transcript: they share no payload anywhere.
    """
    e = engine()
    if run_id in e.runs:
        handle = e.runs[run_id]
        if handle.status == "running":
            raise HTTPException(409, "the encounter is still running")
        case_id = handle.case_id
    else:
        # A stored run is over. It may reveal only a case it actually recorded
        # -- before, any served case's truth came back for any directory name.
        run_dir = _run_dir(run_id)
        if not _recorded(run_dir, case_id):
            raise HTTPException(404, "reveal needs a ?case_id= recorded in this run")

    view = e.store.judge_view(case_id)
    case = e.store.metadata(case_id)
    return {
        "case_id": case_id,
        "correct_diagnosis": view.correct_diagnosis,
        "management_and_follow_up": view.management_and_follow_up,
        # Honesty about the benchmark: on 29 of 214 cases the dataset embeds the
        # diagnosis in the test results the gatekeeper is designed to return, so
        # a correct answer there is not evidence of reasoning.
        "dx_in_results": case.dx_in_results,
        "note": "Ground truth from the benchmark. Not a clinical judgement.",
    }


if CLIENT_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(CLIENT_DIR), html=True), name="client")
