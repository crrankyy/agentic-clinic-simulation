"""The web surface, with the isolation boundary as the main subject.

A spectator UI is allowed to learn the answer *after* the encounter — but the
pipeline's whole design is that ground truth never travels alongside
doctor-visible state. These assert the boundary holds across the HTTP layer,
which is a new place for it to leak.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentclinic.api import app as app_module
from agentclinic.api.engine import SERVED_CASES, replay_events
from agentclinic.api.wire import _EVENT_FIELDS, from_trace, to_wire
from agentclinic.data.views import CaseStore
from agentclinic.graphs.state import Event


class StubPreflight:
    """No test may touch the network; the real check calls OpenRouter."""

    def __init__(self, ok: bool = True, reason: str = "ok") -> None:
        from agentclinic.llm.preflight import PreflightResult

        self.result = PreflightResult(ok=ok, reason=reason)

    async def get(self, *, force: bool = False):
        return self.result


@pytest.fixture
def client(cases):
    app_module._engine = None          # the app builds its engine lazily
    app_module.engine().preflight = StubPreflight()
    with TestClient(app_module.app) as c:
        yield c
    app_module._engine = None


# --- the wire format is a whitelist -----------------------------------------

def test_to_wire_copies_only_named_fields():
    """The default for anything new on `Event` must be "not transmitted"."""
    event = Event(turn=3, kind="answer", actor="patient", text="I have a fever",
                  meta={"unknown": "False", "secret_field": "must not travel"})
    wire = to_wire(event, seq=7)
    assert set(wire) == {"seq", "meta", *_EVENT_FIELDS}
    assert wire["seq"] == 7 and wire["text"] == "I have a fever"
    assert "secret_field" not in wire["meta"], "meta is filtered, not forwarded"
    assert wire["meta"] == {"unknown": "False"}


def test_replay_and_live_produce_identical_payloads():
    """The client must not be able to tell a replay from a live run."""
    event = Event(turn=2, kind="test", actor="gatekeeper", text="WBC 14.2",
                  meta={"tier": "contains", "key": "Complete_Blood_Count"})
    live = to_wire(event, seq=4)
    record = {"kind": "event", "seq": 4, "turn": 2, "event_kind": "test",
              "actor": "gatekeeper", "text": "WBC 14.2",
              "meta": {"tier": "contains", "key": "Complete_Blood_Count"}}
    assert from_trace(record) == live


# --- ground truth ------------------------------------------------------------

def test_no_transcript_route_carries_ground_truth(client, cases):
    """The string-scan test, applied to the HTTP surface.

    Restricted to leak-free cases: on 29 of 214 the dataset itself embeds the
    diagnosis in the test results the gatekeeper is designed to return, so a hit
    there would be a property of the benchmark, not of this server.
    """
    store = CaseStore(cases)
    leak_free = [c for c in SERVED_CASES if not store.metadata(c).dx_in_results]
    assert leak_free, "need at least one leak-free served case for this to mean anything"

    blob = json.dumps([
        client.get("/api/cases").json(),
        client.get("/api/runs").json(),
        client.get("/api/meta").json(),
    ], ensure_ascii=False).lower()

    for case_id in leak_free:
        dx = store.judge_view(case_id).correct_diagnosis.lower()
        assert dx and dx not in blob, f"{case_id}: ground truth reached a transcript route"


def test_cases_route_exposes_only_the_objective(client):
    payload = client.get("/api/cases").json()
    assert {c["case_id"] for c in payload} == set(SERVED_CASES)
    for entry in payload:
        assert set(entry) == {"case_id", "objective"}


def test_reveal_refuses_while_the_encounter_is_running(client):
    from agentclinic.api.engine import RunHandle

    e = app_module.engine()
    handle = RunHandle(run_id="web-panel-test", case_id=SERVED_CASES[0], config="panel")
    e.runs[handle.run_id] = handle                       # status defaults to "running"
    assert client.get(f"/api/runs/{handle.run_id}/reveal").status_code == 409

    handle.finish(status="finished")
    ok = client.get(f"/api/runs/{handle.run_id}/reveal")
    assert ok.status_code == 200
    assert ok.json()["correct_diagnosis"]


def test_reveal_is_the_only_route_that_returns_a_diagnosis(client, cases):
    """Named so it fails loudly if someone adds ground truth to another route."""
    store = CaseStore(cases)
    case_id = SERVED_CASES[0]
    dx = store.judge_view(case_id).correct_diagnosis
    from agentclinic.api.engine import RunHandle

    e = app_module.engine()
    h = RunHandle(run_id="web-panel-done", case_id=case_id, config="panel")
    h.finish(status="finished")
    e.runs[h.run_id] = h

    assert dx.lower() in json.dumps(
        client.get(f"/api/runs/{h.run_id}/reveal").json(), ensure_ascii=False).lower()
    listed = json.dumps(client.get("/api/runs").json(), ensure_ascii=False).lower()
    assert dx.lower() not in listed


# --- the served set ----------------------------------------------------------

def test_an_unserved_case_is_refused_not_substituted(client):
    r = client.post("/api/runs", json={"case_id": "medqa-0001", "config": "panel"})
    assert r.status_code == 404
    assert "not served" in r.json()["detail"]


def test_an_unknown_config_is_refused(client):
    r = client.post("/api/runs", json={"case_id": SERVED_CASES[0], "config": "committee"})
    assert r.status_code == 400


def test_the_served_set_is_the_dev_subset(cases):
    """If the dev subset ever changes, this fails rather than the app drifting."""
    from agentclinic.data.splits import select_eval_subset
    from agentclinic.paths import DATASET_DIR

    splits = json.loads((DATASET_DIR / "splits.json").read_text(encoding="utf-8"))
    assert tuple(select_eval_subset(cases, splits["dev"], n=3)) == SERVED_CASES


# --- replay ------------------------------------------------------------------

def test_replay_reads_events_back_out_of_a_trace(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir(parents=True)
    lines = [
        {"kind": "llm_call", "node": "orchestrator"},          # ignored
        {"kind": "event", "seq": 1, "turn": 1, "event_kind": "answer",
         "actor": "patient", "text": "second", "meta": {}},
        {"kind": "event", "seq": 0, "turn": 0, "event_kind": "objective",
         "actor": "system", "text": "first", "meta": {}},
    ]
    (traces / "medqa-0002.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    events = replay_events(tmp_path, "medqa-0002")
    assert [e["text"] for e in events] == ["first", "second"], "must sort by seq"
    assert all("event_kind" not in e for e in events), "wire shape, not trace shape"


# --- the live path, end to end ------------------------------------------------

async def test_a_live_run_streams_its_transcript_without_ground_truth(cases, tmp_path,
                                                                     monkeypatch):
    """Drives the real `Engine._drive` with a fake model.

    This is the path that actually matters: `astream` diffing, `to_wire`, trace
    persistence and the SSE payload, exercised together. The scan is restricted
    to a leak-free case so a hit means *this code* leaked, not the benchmark.
    """
    from agentclinic.api.engine import Engine, stream_live
    from agentclinic.llm.fake import FakeChatModel
    from agentclinic.llm.openrouter import LLMCaller
    from graph_fixtures import FINAL, HYP, decide

    case_id = "medqa-0009"                       # leak-free (dx not in results)
    store = CaseStore(cases)
    assert not store.metadata(case_id).dx_in_results
    dx = store.judge_view(case_id).correct_diagnosis

    e = Engine()
    monkeypatch.setattr("agentclinic.api.engine.RUNS_DIR", tmp_path)
    # The engine wires the *real* `Patient` agent, so a reply is a script entry.
    # No test is ordered: the gatekeeper's LLM tier would consume entries too,
    # and this test is about the stream, not about matching.
    reply = {"reply": "About two days ago.", "unknown": False}
    script = [HYP, decide("ask_patient", "When did this start?"), reply,
              HYP, decide("finalize"), FINAL]
    model = FakeChatModel(script)

    def fake_caller(tracer):
        return LLMCaller(model, tracer=tracer), None
    monkeypatch.setattr(e, "_caller", fake_caller)

    handle = e.start(case_id=case_id, config="single_doctor", max_turns=10)
    frames = [f async for f in stream_live(handle)]

    events = [f["data"] for f in frames if f["type"] == "event"]
    status = [f["data"] for f in frames if f["type"] == "status"]
    assert status and status[-1]["status"] == "finished", status
    assert len(events) >= 3, events
    assert [e_["seq"] for e_ in events] == list(range(len(events))), "seq must be dense"
    assert {e_["actor"] for e_ in events} <= {"doctor", "patient", "gatekeeper", "system"}

    blob = json.dumps(events, ensure_ascii=False).lower()
    assert dx.lower() not in blob, "ground truth reached the live stream"

    # And the transcript was persisted, so the run is replayable.
    persisted = replay_events(tmp_path / handle.run_id, case_id)
    assert persisted == events, "replay must reproduce the live stream exactly"


async def test_a_failed_run_reports_a_type_not_a_traceback(cases, tmp_path, monkeypatch):
    """An exception can carry prompt text; a traceback carries more."""
    from agentclinic.api.engine import Engine, stream_live

    e = Engine()
    monkeypatch.setattr("agentclinic.api.engine.RUNS_DIR", tmp_path)

    def boom(tracer):
        raise RuntimeError("prompt was: <ground truth here>")
    monkeypatch.setattr(e, "_caller", boom)

    handle = e.start(case_id=SERVED_CASES[0], config="panel", max_turns=5)
    frames = [f async for f in stream_live(handle)]
    status = frames[-1]["data"]
    assert status["status"] == "failed"
    assert status["error"].startswith("RuntimeError:")
    assert "Traceback" not in status["error"]


def test_starting_a_run_through_the_route_actually_schedules_it(client, monkeypatch,
                                                                tmp_path):
    """The route, not just the engine.

    `POST /api/runs` was a sync endpoint, so FastAPI ran it in its threadpool
    where `asyncio.create_task` raises "no running event loop" — every start
    500'd. The earlier engine tests missed it because they called
    `Engine.start()` from inside an async test, where a loop already exists.
    """
    e = app_module.engine()
    monkeypatch.setattr("agentclinic.api.engine.RUNS_DIR", tmp_path)
    started: list[str] = []

    async def fake_drive(handle, *, max_turns):
        started.append(handle.run_id)
        handle.finish(status="finished")
    monkeypatch.setattr(e, "_drive", fake_drive)

    r = client.post("/api/runs", json={"case_id": SERVED_CASES[0],
                                       "config": "single_doctor", "max_turns": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] in e.runs
    assert body["projected_requests"] == 5 * 3 + 3


def test_replay_reports_the_real_stop_reason_not_just_finished(client, tmp_path,
                                                               monkeypatch):
    """A replay that says "Finished" for a capped encounter is a faithful
    transcript with an unfaithful verdict on it."""
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "web-panel-capped"
    (run_dir / "traces").mkdir(parents=True)
    (run_dir / "traces" / f"{SERVED_CASES[0]}.jsonl").write_text(json.dumps(
        {"kind": "event", "seq": 0, "turn": 0, "event_kind": "objective",
         "actor": "system", "text": "referral", "meta": {}}), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(
        {"case_id": SERVED_CASES[0], "stop_reason": "turn_cap"}), encoding="utf-8")

    body = client.get(f"/api/runs/web-panel-capped/stream",
                      params={"case_id": SERVED_CASES[0]}).text
    assert '"stop_reason": "turn_cap"' in body, body


def test_a_dead_key_is_refused_before_the_run_starts(client):
    """M-17: web-panel-8c0fe568 was accepted with a revoked key and failed
    0.9 s later, after three attempts reported as "did not validate"."""
    app_module.engine().preflight = StubPreflight(
        ok=False, reason="OpenRouter rejected the API key (401: User not found).")
    r = client.post("/api/runs", json={"case_id": SERVED_CASES[0], "config": "single_doctor"})
    assert r.status_code == 503
    assert "rejected the API key" in r.json()["detail"]
    meta = client.get("/api/meta").json()
    assert meta["credential"]["ok"] is False


def test_replay_reports_a_failed_run_as_failed(client, tmp_path, monkeypatch):
    """M-19: replay used to call every run on disk "finished"."""
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "web-panel-dead"
    (run_dir / "traces").mkdir(parents=True)
    (run_dir / "traces" / f"{SERVED_CASES[0]}.jsonl").write_text(json.dumps(
        {"kind": "event", "seq": 0, "turn": 0, "event_kind": "objective",
         "actor": "system", "text": "referral", "meta": {}}), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(
        {"case_id": SERVED_CASES[0], "config": "panel", "status": "failed",
         "error_class": "auth", "error": "ProviderAuthError: 401"}), encoding="utf-8")
    listed = [r for r in client.get("/api/runs").json() if r["run_id"] == "web-panel-dead"]
    assert listed and listed[0]["status"] == "failed"
    body = client.get("/api/runs/web-panel-dead/stream",
                      params={"case_id": SERVED_CASES[0]}).text
    assert '"status": "failed"' in body and '"error_class": "auth"' in body


async def test_each_web_run_gets_its_own_spend_tracker(cases, tmp_path, monkeypatch):
    """M-35: one shared tracker turned the per-case cap into a lifetime cap."""
    from agentclinic.api.engine import Engine
    from agentclinic.llm.preflight import PreflightResult  # noqa: F401

    monkeypatch.setattr("agentclinic.api.engine.RUNS_DIR", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    e = Engine()
    _, g1 = e._caller(tracer=None)
    g1.spend.add(SERVED_CASES[0], 0.49)
    _, g2 = e._caller(tracer=None)
    assert g2.spend.case_total(SERVED_CASES[0]) == 0.0
    assert g1.bucket is g2.bucket, "the rate bucket is per account and must be shared"


def test_every_replayable_run_says_which_model_produced_it(client, tmp_path, monkeypatch):
    """A DeepSeek run and a ling run of the same case must not be confused."""
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    for name, model in (("dev-single_doctor-aaaa", "deepseek/deepseek-v4.1-flash"),
                        ("web-single_doctor-bbbb", None)):
        d = tmp_path / name
        (d / "traces").mkdir(parents=True)
        lines = [{"kind": "event", "seq": 0, "turn": 0, "event_kind": "objective",
                  "actor": "system", "text": "referral", "meta": {}},
                 {"kind": "event", "seq": 1, "turn": 1, "event_kind": "stop",
                  "actor": "doctor", "text": "final: X", "meta": {}},
                 {"kind": "node", "node": "runner", "event": "case_end",
                  "stop_reason": "finalize"}]
        (d / "traces" / f"{SERVED_CASES[0]}.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        (d / "finals.json").write_text(json.dumps({SERVED_CASES[0]: {"diagnosis": "X"}}))
        if model:
            (d / "run.json").write_text(json.dumps({"model": model}))
    rows = {r["run_id"]: r for r in client.get("/api/runs").json()}
    assert rows["dev-single_doctor-aaaa"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert rows["web-single_doctor-bbbb"]["model"] is None, "never guessed"
    # A CLI run has no summary.json; its stop reason is the runner's case_end.
    assert rows["dev-single_doctor-aaaa"]["stop_reason"] == "finalize"


def _fake_run(root, name, cases, started, model="deepseek/deepseek-v4.1-flash"):
    d = root / name
    (d / "traces").mkdir(parents=True)
    for cid in cases:
        (d / "traces" / f"{cid}.jsonl").write_text("\n".join(json.dumps(x) for x in [
            {"ts": started, "kind": "event", "seq": 0, "turn": 0, "event_kind": "objective",
             "actor": "system", "text": "referral", "meta": {}},
            {"ts": started, "kind": "event", "seq": 1, "turn": 1, "event_kind": "stop",
             "actor": "doctor", "text": "final: X", "meta": {}}]), encoding="utf-8")
    (d / "run.json").write_text(json.dumps({"config": "single_doctor", "model": model,
                                            "started_utc": started}))
    (d / "finals.json").write_text(json.dumps({c: {"diagnosis": "X"} for c in cases}))
    return d


def test_runs_come_back_latest_first_and_include_every_recorded_case(client, tmp_path, monkeypatch):
    """A 10-case run must show all ten, not only the three served for live runs."""
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "dev-single_doctor-old", ["medqa-0009"], "2026-09-15T10:00:00+00:00")
    _fake_run(tmp_path, "dev-single_doctor-new", ["medqa-0031", "medqa-0013"],
              "2026-09-28T10:00:00+00:00")
    rows = client.get("/api/runs").json()
    assert [(r["run_id"], r["case_id"]) for r in rows] == [
        ("dev-single_doctor-new", "medqa-0013"), ("dev-single_doctor-new", "medqa-0031"),
        ("dev-single_doctor-old", "medqa-0009")]
    body = client.get("/api/runs/dev-single_doctor-new/stream",
                      params={"case_id": "medqa-0031"}).text
    assert '"status": "finished"' in body


def test_reveal_answers_only_for_a_case_the_run_recorded(client, tmp_path, monkeypatch):
    """Before, any served case's truth came back for any directory name --
    including '..'."""
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "dev-single_doctor-r", ["medqa-0031"], "2026-09-28T10:00:00+00:00")
    assert client.get("/api/runs/dev-single_doctor-r/reveal",
                      params={"case_id": "medqa-0031"}).status_code == 200
    assert client.get("/api/runs/dev-single_doctor-r/reveal",
                      params={"case_id": "medqa-0002"}).status_code == 404
    for bad in ("..", ".", "..%2F..", "x..y/../z"):
        r = client.get(f"/api/runs/{bad}/reveal", params={"case_id": "medqa-0002"})
        assert r.status_code == 404, bad
    r = client.get("/api/runs/dev-single_doctor-r/stream",
                   params={"case_id": "../../../etc/passwd"})
    assert r.status_code == 404
