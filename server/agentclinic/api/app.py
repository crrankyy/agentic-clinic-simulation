"""FastAPI surface for watching an encounter unfold.

Educational simulation only. Never medical advice, never real patient data.

The route layout encodes the isolation rule rather than documenting it: every
transcript route is built from doctor-visible state, and ground truth exists on
exactly one route (`/reveal`) which refuses to answer while an encounter is
still running. There is no code path on which the two share a payload.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import load_budgets, load_dotenv, load_models
from ..paths import CLIENT_DIR, RUNS_DIR
from .engine import CONFIGS, Engine, replay_events, stream_live

DISCLAIMER = (
    "Educational simulation only. Not medical advice. This is a simulation over "
    "public benchmark data and says nothing about clinical performance. It must "
    "never be presented or used as medical advice, and must never process real "
    "patient data."
)

app = FastAPI(title="AgentClinic — encounter viewer", version="0.1.0")

# The client is served from this same app in normal use. CORS is here only so a
# separate dev server (vite, `python -m http.server`) can talk to it locally.
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)

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
def meta() -> dict[str, Any]:
    models = load_models()
    budgets = load_budgets()
    return {
        "disclaimer": DISCLAIMER,
        "model": models.for_role("orchestrator"),
        "provider_pin": models.provider.pin,
        "configs": list(CONFIGS),
        "daily_remaining": engine().daily_remaining(),
        "daily_limit": budgets.requests_per_day,
    }


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
         "status": h.status, "source": "live", "events": len(h.events)}
        for h in e.runs.values()
    ]
    served = set(e.case_ids())
    if RUNS_DIR.is_dir():
        for d in sorted(RUNS_DIR.iterdir(), reverse=True):
            if not d.is_dir() or d.name in e.runs:
                continue
            traces = d / "traces"
            if not traces.is_dir():
                continue
            for trace in sorted(traces.glob("*.jsonl")):
                case_id = trace.stem
                if case_id not in served:
                    continue
                n = len(replay_events(d, case_id))
                if not n:
                    continue  # recorded before events were persisted
                out.append({"run_id": d.name, "case_id": case_id,
                            "config": "panel" if "panel" in d.name else "single_doctor",
                            "status": "finished", "source": "replay", "events": n})
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

    # The same pre-flight the CLI refuses on. Discovering the daily cap mid-run
    # turns a watchable encounter into a wall of 429s.
    projected = e.projected_requests(body.config, body.max_turns)
    remaining = e.daily_remaining()
    if projected > remaining:
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
        run_dir = RUNS_DIR / run_id
        if not run_dir.is_dir():
            raise HTTPException(404, f"no run {run_id!r}")
        if case_id is None or case_id not in e.case_ids():
            raise HTTPException(400, "replay needs a served ?case_id=")
        events = replay_events(run_dir, case_id)
        if not events:
            raise HTTPException(404, "that run has no persisted transcript "
                                     "(recorded before events were saved)")
        final = None
        finals_path = run_dir / "finals.json"
        if finals_path.exists():
            final = json.loads(finals_path.read_text(encoding="utf-8")).get(case_id)
        summary_path = run_dir / "summary.json"
        stop_reason = (json.loads(summary_path.read_text(encoding="utf-8")).get("stop_reason")
                       if summary_path.exists() else None)

        async def replay() -> AsyncIterator[str]:
            for ev in events[start:]:
                yield _sse({"type": "event", "data": ev})
            yield _sse({"type": "status", "data": {
                "status": "finished", "stop_reason": stop_reason, "final": final,
                "error": None, "run_id": run_id, "case_id": case_id,
                "config": "panel" if "panel" in run_id else "single_doctor"}})
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
        if not (RUNS_DIR / run_id).is_dir():
            raise HTTPException(404, f"no run {run_id!r}")
        if case_id is None:
            raise HTTPException(400, "reveal needs a ?case_id= for a stored run")
    if case_id not in e.case_ids():
        raise HTTPException(404, f"case {case_id!r} is not served")

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
