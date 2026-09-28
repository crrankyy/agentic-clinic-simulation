"""Regression tests for the 2026-09-27 sim review (D-056 .. D-062).

Each test names the finding it guards and was written against a failure seen in
a recorded run. Where it matters, the test checks behaviour -- what reached the
gatekeeper, whether a turn was spent -- not merely that some text was rendered:
D-055's test checked that a marker was rendered, passed, and the behaviour it
was meant to prevent carried on (lesson 2).
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from agentclinic.agents.gatekeeper import UNAVAILABLE, Gatekeeper
from agentclinic.agents.patient import build_prompt
from agentclinic.config import load_test_costs
from agentclinic.data.views import CaseStore
from agentclinic.graphs.ledger import build_ledger, check_repeat
from agentclinic.graphs.nodes import patient_history
from agentclinic.graphs.routing import route_stop
from agentclinic.graphs.state import Event, new_state
from agentclinic.llm.fake import FakeChatModel
from agentclinic.llm.guards import (
    BudgetExceeded,
    DailyRequestCounter,
    RunGuards,
    SpendTracker,
    TokenBucket,
)
from agentclinic.llm.openrouter import (
    LLMCaller,
    ProviderAuthError,
    ProviderUnavailable,
    RetryPolicy,
)
from graph_fixtures import FINAL, HYP, ScriptedPatient, build, decide

CFG = {"recursion_limit": 150}


async def run(graph, store, case_id):
    view = store.doctor_view(case_id)
    return await graph.ainvoke(new_state(case_id, view.objective_for_doctor), CFG)


def _err(cls, status: int, msg: str = "x"):
    resp = httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai/x"))
    return cls(message=msg, response=resp, body=None)


def gate_calls(out, kind="test"):
    return [e for e in out["encounter_log"] if e.kind == kind and e.actor == "gatekeeper"]


# --- M-01: the repeat guard, on the real graph ---------------------------------------

async def test_a_refused_test_reordered_in_new_words_never_reaches_the_gatekeeper(cases):
    """1491dcac t7: "CSF JC virus PCR" after "CSF JC virus PCR and JC virus
    antibody testing" was refused. The re-order must be rejected before it
    executes -- no second gatekeeper call, no turn spent."""
    script = [
        HYP, decide("order_test", "CSF JC virus PCR and JC virus antibody testing"),
        HYP, decide("order_test", "CSF JC virus PCR"),          # rejected by the guard
        decide("ask_patient", "Have you noticed any weakness in your arms?"),
        HYP, decide("finalize"), FINAL,
    ]
    graph, model, store = build(cases, script, case_id="medqa-0002")
    out = await run(graph, store, "medqa-0002")

    assert len(gate_calls(out)) == 1, "the refused test was ordered twice"
    assert out["turn"] == 2, "a blocked proposal must not consume a turn"
    guards = [e for e in out["encounter_log"] if e.kind == "guard"]
    assert len(guards) == 1 and "CSF JC virus PCR" in guards[0].text
    repair = [p for p in model.rendered_prompts if "did not match the required schema" in p]
    assert repair and "NOT part of this case record" in repair[0]


async def test_a_second_order_for_a_delivered_record_is_rejected(cases):
    """medqa-0009: two tests, nine orders. A request that resolves to a record
    already returned is blocked, whatever it is called."""
    script = [
        HYP, decide("order_test", "MRI brain"),
        HYP, decide("order_test", "Brain MRI with contrast"),  # same key: rejected
        decide("finalize"), FINAL,
    ]
    graph, _, store = build(cases, script, case_id="medqa-0002")
    out = await run(graph, store, "medqa-0002")
    assert len(gate_calls(out)) == 1
    assert any("already received this result" in (e.meta or {}).get("reason", "")
               for e in out["encounter_log"] if e.kind == "guard")


async def test_a_genuinely_different_test_is_not_blocked(cases):
    case = next(c for c in cases if c.case_id == "medqa-0012")
    first, second = list(case.test_results)[:2]
    script = [HYP, decide("order_test", first), HYP, decide("order_test", second),
              HYP, decide("finalize"), FINAL]
    graph, _, store = build(cases, script, case_id="medqa-0012")
    out = await run(graph, store, "medqa-0012")
    assert len(gate_calls(out)) == 2
    assert not any(e.kind == "guard" for e in out["encounter_log"])


async def test_when_every_proposal_repeats_the_encounter_ends_as_a_clinical_stop(cases):
    """Not a parse failure: the output was well-formed. Finalize runs normally
    and the case is scored."""
    csf = "CSF JC virus PCR"
    script = [HYP, decide("order_test", csf), HYP,
              decide("order_test", csf), decide("order_test", csf + " test"),
              decide("order_test", "repeat " + csf), FINAL]
    graph, _, store = build(cases, script, case_id="medqa-0002")
    out = await run(graph, store, "medqa-0002")
    assert out["stop_reason"] == "no_new_actions"
    assert out["final"].rationale == FINAL["rationale"], "finalize must call the model"
    assert out.get("parse_failures", 0) == 0, "guard rejections are not parse failures"


async def test_a_repeated_question_is_blocked_and_a_new_one_is_not(cases):
    """1d3154ff t6-t8: "Has natalizumab been discontinued..." three times."""
    patient = ScriptedPatient(reply="I'm not sure.", unknown=True)
    script = [
        HYP, decide("ask_patient", "Has natalizumab been discontinued, and if so, for how long?"),
        HYP, decide("ask_patient", "Has natalizumab been discontinued, and when was your last dose?"),
        decide("ask_patient", "Do you have any numbness or tingling in your hands?"),
        HYP, decide("finalize"), FINAL,
    ]
    graph, model, store = build(cases, script, case_id="medqa-0002", patient=patient)
    out = await run(graph, store, "medqa-0002")
    assert len(patient.asked) == 2
    assert "numbness" in patient.asked[1]
    assert sum(1 for e in out["encounter_log"] if e.kind == "guard") == 1


async def test_the_orchestrator_sees_the_ledger_but_never_a_key_or_an_answer(cases):
    patient = ScriptedPatient(reply="UNIQUE_ANSWER_TEXT_QRS", unknown=True)
    script = [HYP, decide("ask_patient", "What is your HIV status?"),
              HYP, decide("order_test", "MRI brain"),
              HYP, decide("finalize"), FINAL]
    graph, model, store = build(cases, script, case_id="medqa-0002", patient=patient)
    await run(graph, store, "medqa-0002")
    last = [p for p in model.rendered_prompts if "Choose exactly one action" in p][-1]
    assert "What is your HIV status? -- the patient could not answer" in last
    assert "MRI brain -- result received" in last
    assert "UNIQUE_ANSWER_TEXT_QRS" not in last, "answer text reached the orchestrator"
    assert "MRI_Brain" not in last, "a gatekeeper key reached the orchestrator"


def test_the_guard_replayed_on_1491dcac_blocks_only_repeats():
    """The recorded transcript: every block must be a genuine repeat."""
    from agentclinic.paths import RUNS_DIR

    path = RUNS_DIR / "web-single_doctor-1491dcac" / "traces" / "medqa-0002.jsonl"
    if not path.exists():
        pytest.skip("recorded run not present")
    events = [Event(turn=r["turn"], kind=r["event_kind"], actor=r["actor"], text=r["text"],
                    meta=r.get("meta") or {})
              for r in map(json.loads, path.read_text().splitlines()) if r.get("kind") == "event"]
    act = {"question": "ask_patient", "test": "order_test"}
    blocked = []
    for i, e in enumerate(events):
        if e.actor == "doctor" and e.kind in act:
            if check_repeat(act[e.kind], e.text,
                            build_ledger([x for x in events[:i] if x.turn < e.turn])):
                blocked.append(e.turn)
    # t7/t10: CSF re-orders; t14: HIV test re-order; t9/t12/t13/t16/t17: HIV
    # questions already asked. Measured, and each checked by hand.
    assert blocked == [7, 9, 10, 12, 13, 14, 16, 17]


# --- M-02 / M-13 / M-14: the caller's failure handling -------------------------------

def _caller(script, **kw):
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)

    policy = kw.pop("retry", RetryPolicy(transient_attempts=3, timeout_retries=1))
    caller = LLMCaller(FakeChatModel(script), retry=policy, sleep=sleep, **kw)
    return caller, sleeps


async def test_a_revoked_key_fails_once_and_is_never_re_prompted():
    """web-panel-8c0fe568: three attempts in 166 ms, "did not validate"."""
    from agentclinic.graphs.schemas import HypothesisUpdate

    caller, sleeps = _caller([_err(openai.AuthenticationError, 401, "User not found.")])
    with pytest.raises(ProviderAuthError):
        await caller.structured(HypothesisUpdate, "p", case_id="c", node="hypothesis")
    assert len(caller.model.calls) == 1 and sleeps == []


async def test_a_rate_limit_backs_off_and_resends_the_same_prompt():
    from agentclinic.graphs.schemas import HypothesisUpdate

    caller, sleeps = _caller([_err(openai.RateLimitError, 429), HYP])
    out = await caller.structured(HypothesisUpdate, "THE PROMPT", case_id="c", node="hypothesis")
    assert out.differential
    prompts = caller.model.rendered_prompts
    assert prompts[0] == prompts[1] == "THE PROMPT", "a 429 must not become a repair prompt"
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert caller.parse_failures_for("c") == 0 and caller.transient_failures_for("c") == 1


async def test_a_persistent_rate_limit_becomes_provider_unavailable():
    from agentclinic.graphs.schemas import HypothesisUpdate

    caller, sleeps = _caller([_err(openai.RateLimitError, 429)] * 4)
    with pytest.raises(ProviderUnavailable):
        await caller.structured(HypothesisUpdate, "p", case_id="c", node="hypothesis")
    assert len(sleeps) == 3


async def test_a_text_reply_instead_of_the_tool_call_is_repaired_not_returned():
    """M-11: LangChain returns None; the node used to crash on it."""
    from agentclinic.graphs.schemas import HypothesisUpdate

    caller, _ = _caller([None, HYP])
    out = await caller.structured(HypothesisUpdate, "p", case_id="c", node="hypothesis")
    assert out is not None and out.differential
    assert "did not call the required tool" in caller.model.rendered_prompts[1]


async def test_a_failed_call_does_not_recount_the_previous_calls_cost(tmp_path):
    """M-14: the shared slot re-added the last call's cost on every failure."""
    from agentclinic.graphs.schemas import HypothesisUpdate

    guards = RunGuards(bucket=TokenBucket(rate_per_minute=6000),
                       daily=DailyRequestCounter(tmp_path / "d.json", limit=None),
                       spend=SpendTracker(per_case_cap=10, per_run_cap=10))
    caller, _ = _caller([_err(openai.RateLimitError, 429), HYP], guards=guards)
    await caller.structured(HypothesisUpdate, "p", case_id="c", node="hypothesis")
    assert guards.spend.case_total("c") == 0.0


# --- M-10: a provider outage ends the case cleanly -----------------------------------

async def test_a_provider_outage_in_hypothesis_finalizes_instead_of_crashing(cases):
    script = [ProviderUnavailable("rate_limited", 429, "still 429 after 5 attempts")]
    graph, _, store = build(cases, script, case_id="medqa-0002")
    out = await run(graph, store, "medqa-0002")
    assert out["stop_reason"] == "provider_error"
    assert out["final"] is not None and "Forced finalize" in out["final"].rationale
    assert any(e.kind == "provider_error" for e in out["encounter_log"])


# --- M-07: the last result is read before a turn-cap finalize ------------------------

async def test_the_last_answer_is_read_before_a_turn_cap_finalize(cases):
    patient = ScriptedPatient(reply="UNIQUE_LAST_ANSWER_ZZ")
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2"), HYP, FINAL]
    graph, model, store = build(cases, script, case_id="medqa-0002", patient=patient, max_turns=2)
    out = await run(graph, store, "medqa-0002")
    assert out["stop_reason"] == "turn_cap"
    hyp = [p for p in model.rendered_prompts if "working differential" in p]
    assert len(hyp) == 3 and "UNIQUE_LAST_ANSWER_ZZ" in hyp[-1]


def test_route_stop_absorbs_only_a_turn_cap():
    assert route_stop({"stop_reason": "turn_cap"}, absorb_on_cap=True) == "absorb"
    assert route_stop({"stop_reason": "turn_cap", "budget_exhausted": True},
                      absorb_on_cap=True) == "stop"
    assert route_stop({"stop_reason": "provider_error"}, absorb_on_cap=True) == "stop"
    assert route_stop({"stop_reason": "turn_cap"}) == "stop"


# --- M-20 / M-26: the hypothesis reads evidence, not its own past output -------------

async def test_the_hypothesis_transcript_is_evidence_only(cases):
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("finalize"), FINAL]
    graph, model, store = build(cases, script, case_id="medqa-0002")
    await run(graph, store, "medqa-0002")
    second = [p for p in model.rendered_prompts if "working differential" in p][1]
    transcript = second[second.index("## What has happened so far"):]
    assert "leading:" not in transcript, "the hypothesis re-read its own past guess"
    assert "## Your previous assessment" in second and "Endometritis" in second


# --- M-09 / M-23 / M-27 / M-08 / M-03: the gatekeeper --------------------------------

@pytest.fixture
def gk0002(cases):
    return Gatekeeper(CaseStore(cases).gatekeeper_view("medqa-0002"), load_test_costs())


@pytest.mark.parametrize("request_, expected", [
    ("MRI spine", None),                         # was MRI_Brain -- a wrong region
    ("MRI cervical spine", None),
    ("MRI spinal cord", None),
    ("magnetic resonance imaging of the brain", "MRI_Brain"),
    ("MRI (brain).", "MRI_Brain"),               # punctuation, M-23
    ("MRI of the brain, with contrast", "MRI_Brain"),
])
def test_the_gatekeeper_never_returns_a_different_region(gk0002, request_, expected):
    assert gk0002.resolve_deterministic(request_, "tests") == expected


@pytest.mark.parametrize("request_", ["neurological exam", "Neuro exam", "neurologic examination"])
def test_common_exam_phrasings_match_deterministically(gk0002, request_):
    assert gk0002.resolve_deterministic(request_, "exams") == "Neurological_Examination"


async def test_a_bundled_request_is_marked_partial(gk0002):
    both = await gk0002.respond("MRI brain and spinal cord (with and without contrast)")
    assert both.outcome == "partial"
    qualified = await gk0002.respond("MRI brain with and without contrast")
    assert qualified.outcome == "result"


async def test_a_second_order_for_a_delivered_record_says_so_without_the_payload(gk0002):
    reply = await gk0002.respond("MRI brain", "tests", {"MRI_Brain": 2})
    assert reply.outcome == "repeat" and reply.ref_turn == 2
    assert "same record already reported at turn 2" in reply.text
    assert "PML" not in reply.text


async def test_a_failed_model_call_is_never_turned_into_a_refusal(cases):
    """M-03: in c79bb4e6 three 429s became "not part of the case record at all"."""
    async def outage(request, candidates):
        raise ProviderUnavailable("rate_limited", 429, "429 after retries")

    gk = Gatekeeper(CaseStore(cases).gatekeeper_view("medqa-0002"), load_test_costs(),
                    llm_disambiguate=outage)
    with pytest.raises(ProviderUnavailable):
        await gk.respond("CSF JC virus PCR")

    async def breach(request, candidates):
        raise BudgetExceeded("spend_cap", "cap")

    gk = Gatekeeper(CaseStore(cases).gatekeeper_view("medqa-0002"), load_test_costs(),
                    llm_disambiguate=breach)
    with pytest.raises(BudgetExceeded):
        await gk.respond("CSF JC virus PCR")


async def test_the_disambiguator_only_treats_a_content_failure_as_no_match(cases):
    from agentclinic.agents.gatekeeper import make_llm_disambiguator

    caller = LLMCaller(FakeChatModel([_err(openai.AuthenticationError, 401)]))
    disambiguate = make_llm_disambiguator(caller, "medqa-0002")
    with pytest.raises(ProviderAuthError):
        await disambiguate("CSF JC virus PCR", ("MRI_Brain",))
    caller = LLMCaller(FakeChatModel([{"entry": "NONE"}]))
    assert await make_llm_disambiguator(caller, "medqa-0002")("CSF", ("MRI_Brain",)) is None


# --- M-21 / M-22 / M-28: the patient --------------------------------------------------

def test_the_patient_gets_its_own_exchanges_and_never_a_result():
    log = [
        Event(turn=1, kind="question", actor="doctor", text="What is your HIV status?"),
        Event(turn=1, kind="answer", actor="patient", text="I don't know."),
        Event(turn=2, kind="test", actor="doctor", text="MRI brain"),
        Event(turn=2, kind="test", actor="gatekeeper", text="MRI Brain: lesions of PML"),
    ]
    history = patient_history(log)
    assert history == [("What is your HIV status?", "I don't know.")]
    assert "PML" not in json.dumps(history)


def test_the_patient_prompt_carries_its_history_and_the_consistency_rules(cases):
    view = CaseStore(cases).patient_view("medqa-0002")
    prompt = build_prompt(view, "Have you been tested for HIV?",
                          history=[("What is your HIV status?", "I don't know my status.")])
    assert "Doctor: What is your HIV status?\nYou: I don't know my status." in prompt
    assert "Stay consistent" in prompt
    assert "Anything your case file states is something you know" in prompt
    assert "Answer the whole of what you were asked" in prompt


# --- M-38 / lesson 36: the runner ------------------------------------------------------

async def test_a_dead_key_aborts_the_evaluation_instead_of_crashing_each_case(cases):
    from agentclinic.eval.runner import run_evaluation

    attempted: list[str] = []

    class Dead:
        def __init__(self, case_id):
            self.case_id = case_id

        async def astream(self, *a, **kw):
            attempted.append(self.case_id)
            raise ProviderAuthError("auth", 401, "User not found")
            yield  # pragma: no cover

    chosen = [c for c in cases if c.case_id in ("medqa-0002", "medqa-0009", "medqa-0012")]
    results = await run_evaluation(cases=chosen, store=CaseStore(cases),
                                   build_graph=lambda cid: Dead(cid), judge=None,
                                   recursion_limit=50, concurrency=1)
    assert len(attempted) == 1, "later cases must not spend a request to learn the same thing"
    assert all(r.outcome == "error" for r in results)
    assert sum("aborted" in (r.error or "") for r in results) == 2


async def test_a_crashed_case_keeps_its_partial_transcript(cases, tmp_path):
    from agentclinic.eval.runner import run_case
    from agentclinic.tracing import Tracer

    class BreaksOnSecondQuestion(ScriptedPatient):
        async def answer(self, question, *, case_id, history=()):
            if self.asked:
                raise RuntimeError("patient exploded")
            return await super().answer(question, case_id=case_id)

    case = next(c for c in cases if c.case_id == "medqa-0002")
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2")]

    def factory(case_id):
        graph, _, _ = build(cases, script, case_id=case_id, patient=BreaksOnSecondQuestion())
        return graph

    tracer = Tracer(run_dir=tmp_path)
    result = await run_case(case=case, store=CaseStore(cases), build_graph=factory,
                            judge=None, recursion_limit=100, tracer=tracer)
    assert result.outcome == "crash"
    assert result.patient_questions == 1, "the counters before the crash were thrown away"
    lines = (tmp_path / "traces" / "medqa-0002.jsonl").read_text().splitlines()
    assert any('"kind": "event"' in line for line in lines)


# --- M-17: the preflight, with every network call mocked -----------------------------

@pytest.fixture
def paid_models():
    from agentclinic.config import load_models

    return load_models()


def openrouter(routes):
    """A client whose requests are answered from `{(method, path): (status, json)}`."""
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes[(request.method, request.url.path.removeprefix("/api/v1"))]
        return httpx.Response(status, json=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_preflight_reports_a_revoked_key(paid_models):
    from agentclinic.llm.preflight import preflight

    client = openrouter({("GET", "/key"): (401, {"error": {"message": "User not found.",
                                                           "code": 401}})})
    r = await preflight(paid_models, api_key="sk-test", client=client)
    assert not r.ok and "rejected the API key" in r.reason and "User not found" in r.reason


async def test_preflight_reports_an_account_guardrail_the_listing_cannot_see(paid_models):
    """2026-09-27: the listing said first-party DeepSeek served the model; every
    real call was refused by the account's training guardrail."""
    from agentclinic.llm.preflight import preflight

    pin = paid_models.provider.pin[0]
    client = openrouter({
        ("GET", "/key"): (200, {"data": {"limit": 35, "limit_remaining": 1.2}}),
        ("GET", f"/models/{paid_models.model}/endpoints"): (200, {"data": {"endpoints": [
            {"provider_name": pin, "supported_parameters": ["tools", "tool_choice"]}]}}),
        ("POST", "/chat/completions"): (404, {"error": {
            "message": "No endpoints found. Filter by Guardrails removed it "
                       "(Paid model training violation (account settings))"}}),
    })
    listing_only = await preflight(paid_models, api_key="sk-test", client=client)
    routed = await preflight(paid_models, api_key="sk-test", client=client, routing=True)
    assert listing_only.ok, "the listing alone cannot see the guardrail"
    assert not routed.ok and "training violation" in routed.reason


async def test_preflight_refuses_an_exhausted_spend_limit_on_a_paid_model(paid_models):
    from agentclinic.llm.preflight import preflight

    client = openrouter({("GET", "/key"): (200, {"data": {"limit": 0, "limit_remaining": 0}})})
    r = await preflight(paid_models, api_key="sk-test", client=client)
    assert not r.ok and "spend limit is exhausted" in r.reason


def test_the_cli_probe_and_run_commands_import(tmp_path):
    """`probe` shipped without `import asyncio` and failed on first use; no test
    exercised the command. This at least loads every command."""
    from typer.testing import CliRunner

    from agentclinic.cli import app

    result = CliRunner().invoke(app, ["probe", "--help"])
    assert result.exit_code == 0, result.output


def test_play_builds_a_real_patient_without_crashing(cases, monkeypatch):
    """`play` without --stub-patient referenced an undefined `budgets` and died
    with a NameError before the first prompt. Quitting at once makes no call."""
    from typer.testing import CliRunner

    from agentclinic.cli import app

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    result = CliRunner().invoke(app, ["play", "medqa-0002"], input="quit\n")
    assert result.exit_code == 0, result.output


def test_every_result_field_survives_the_csv_and_rejudge_round_trip(tmp_path):
    """The judge command copied a hand-kept list of fields; every field added
    later came back as its default -- "actions that produced nothing new: 0"
    in a report for a run that had one."""
    import csv
    import dataclasses

    from agentclinic.eval.runner import CaseResult, result_from_row, write_results_csv

    original = CaseResult(
        case_id="medqa-0002", outcome="scored", stop_reason="finalize", forced_stop=False,
        match_type="exact", turns=3, patient_questions=1, tests_ordered=2, unlisted_tests=1,
        match_tiers={"contains": 1, "unmatched": 1}, red_flag_turn=None, test_cost_usd=180.0,
        guard_blocks=2, repeat_orders=1, no_yield_actions=1, actions_after_leader_settled=2,
        transient_failures=3, differential=["A", "B"], final_confidence=0.9,
    )
    for row in (original.row(), next(iter(_csv_rows(original, tmp_path)))):
        back = result_from_row(row)
        for f in dataclasses.fields(CaseResult):
            if f.name in ("judge_correct", "lenient_correct", "top_1", "top_3", "top_5",
                          "in_differential", "latency_s"):
                continue
            assert getattr(back, f.name) == getattr(original, f.name), f.name


def _csv_rows(result, tmp_path):
    import csv

    from agentclinic.eval.runner import write_results_csv

    path = tmp_path / "results.csv"
    write_results_csv([result], path)
    return list(csv.DictReader(path.open()))
