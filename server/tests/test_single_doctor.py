"""End-to-end behaviour of the automated encounter graph, entirely offline."""

from __future__ import annotations

import pytest

from agentclinic.graphs.state import new_state
from agentclinic.llm.guards import (
    BudgetExceeded,
    DailyRequestCounter,
    RunGuards,
    SpendTracker,
    TokenBucket,
)

from graph_fixtures import FINAL, HYP, ScriptedPatient, build, decide

CFG = {"recursion_limit": 100}


async def run(graph, store, case_id="medqa-0010"):
    view = store.doctor_view(case_id)
    return await graph.ainvoke(new_state(case_id, view.objective_for_doctor), CFG)


async def test_full_encounter_reaches_a_final_answer(cases):
    graph, _, store = build(cases, [HYP, decide("ask_patient", "when?"),
                                    HYP, decide("finalize"), FINAL])
    out = await run(graph, store)
    assert out["stop_reason"] == "finalize"
    assert out["final"].diagnosis == "Endometritis"
    assert out["turn"] == 1


@pytest.mark.parametrize("action,argument", [
    ("ask_patient", "when did it start?"),
    ("request_exam", "Vital_Signs_at_Presentation"),
    ("order_test", "CBC"),
])
async def test_every_enabled_action_is_routable(cases, action, argument):
    graph, _, store = build(cases, [HYP, decide(action, argument),
                                    HYP, decide("finalize"), FINAL])
    out = await run(graph, store)
    assert out["final"] is not None
    assert any(e.actor in {"patient", "gatekeeper"} for e in out["encounter_log"])


async def test_turn_cap_forces_a_final_answer(cases):
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2"), HYP, FINAL]
    graph, _, store = build(cases, script, max_turns=2)
    out = await run(graph, store)
    assert out["stop_reason"] == "turn_cap" and out["final"] is not None


async def test_encounter_log_is_never_duplicated_across_the_subgraph(cases):
    """The subgraph returns `panel_events`; `encounter_log` never crosses."""
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2"),
              HYP, decide("finalize"), FINAL]
    graph, _, store = build(cases, script)
    out = await run(graph, store)
    questions = [e for e in out["encounter_log"] if e.kind == "question"]
    assert [e.text for e in questions] == ["q1", "q2"]


async def test_parse_failure_forces_finalize_with_a_distinct_stop_reason(cases):
    """Must never be confused with a clinical abstention (Q-15)."""
    graph, _, store = build(cases, [HYP, {"nonsense": 1}, {"nonsense": 2}, {"nonsense": 3}])
    out = await run(graph, store)
    assert out["stop_reason"] == "parse_failure"
    assert out["final"] is not None and out["parse_failures"] >= 3


async def test_budget_breach_finalizes_without_another_model_call(cases, tmp_path):
    """The budget is what ran out, so finalize must not call a model."""
    spend = SpendTracker(per_case_cap=0.0)
    spend.add("medqa-0010", 1.0)
    guards = RunGuards(
        bucket=TokenBucket(rate_per_minute=6000),
        daily=DailyRequestCounter(tmp_path / "daily.json", limit=10_000),
        spend=spend,
    )
    graph, model, store = build(cases, [HYP], guards=guards)
    out = await run(graph, store)
    assert out["budget_exhausted"] is True
    assert out["stop_reason"] == "budget_exhausted"
    assert out["final"] is not None and out["final"].abstain is True


async def test_crash_surfaces_rather_than_hanging(cases):
    """An exhausted script means the graph looped further than expected."""
    from agentclinic.llm.fake import ScriptExhausted

    graph, _, store = build(cases, [HYP, decide("ask_patient", "q")])
    with pytest.raises(ScriptExhausted):
        await run(graph, store)


# --- isolation, Phase 3 half of PLAN.md §3.3 -------------------------------

async def test_ground_truth_never_appears_in_a_prompt_for_leak_free_cases(cases):
    """The string-scan test the brief requires, scoped to leak-free cases."""
    leak_free = [c for c in cases if not c.dx_in_results and c.test_results][:6]
    for case in leak_free:
        key = next(iter(case.test_results))
        script = [HYP, decide("order_test", key), HYP, decide("finalize"), FINAL]
        graph, model, store = build(cases, script, case_id=case.case_id)
        out = await run(graph, store, case.case_id)
        needle = case.correct_diagnosis.casefold()
        for prompt in model.rendered_prompts:
            assert needle not in prompt.casefold(), f"{case.case_id} leaked into a prompt"
        assert needle not in repr(out).casefold()


async def test_orchestrator_prompt_contains_no_raw_event_text(cases):
    """Structural isolation: the decision subgraph cannot see `encounter_log`."""
    case = next(c for c in cases if not c.dx_in_results and c.test_results)
    key = next(iter(case.test_results))
    patient = ScriptedPatient(reply="UNIQUE_PATIENT_SENTENCE_XYZ")
    script = [HYP, decide("ask_patient", "q"), HYP, decide("finalize"), FINAL]
    graph, model, store = build(cases, script, case_id=case.case_id, patient=patient)
    await run(graph, store, case.case_id)

    orchestrator_prompts = [p for p in model.rendered_prompts if "Choose exactly one action" in p]
    assert orchestrator_prompts, "no orchestrator prompt was rendered"
    for prompt in orchestrator_prompts:
        assert "UNIQUE_PATIENT_SENTENCE_XYZ" not in prompt, (
            "the orchestrator saw raw event text; it must work from the summary"
        )


async def test_hypothesis_does_see_the_transcript(cases):
    """The counterpart: hypothesis must read the log to derive the summary."""
    patient = ScriptedPatient(reply="UNIQUE_PATIENT_SENTENCE_XYZ")
    script = [HYP, decide("ask_patient", "q"), HYP, decide("finalize"), FINAL]
    graph, model, store = build(cases, script, patient=patient)
    await run(graph, store)
    hypothesis_prompts = [p for p in model.rendered_prompts if "working differential" in p]
    assert any("UNIQUE_PATIENT_SENTENCE_XYZ" in p for p in hypothesis_prompts)


async def test_budget_breach_inside_ask_patient_does_not_crash_the_case(cases, tmp_path):
    """The blocker the Phase 3 review found: only 2 of 8 nodes were guarded.

    A breach first seen in `ask_patient` used to escape as an exception, the
    runner recorded `crash`, and the case left the accuracy denominator. With
    `DailyCapExceeded` that would happen to every case after the allowance ran
    out, shrinking the denominator silently.
    """

    class BreachingPatient:
        async def answer(self, question, *, case_id, history=()):
            from agentclinic.llm.guards import BudgetExceeded

            raise BudgetExceeded("spend_cap", "simulated breach inside ask_patient")

    graph, _, store = build(
        cases, [HYP, decide("ask_patient", "q")], patient=BreachingPatient()
    )
    out = await run(graph, store)
    assert out["budget_exhausted"] is True
    assert out["stop_reason"] == "budget_exhausted"
    assert out["final"] is not None, "a budget breach must still produce a final answer"


async def test_budget_breach_inside_finalize_still_produces_an_answer(cases, tmp_path):
    """`finalize` is guarded with skip_if_exhausted=False — nothing follows it.

    The breach must land *at* finalize, after a differential exists, which is
    the case that distinguishes a usable forced answer from an abstention.
    """

    class BreachOnNthCheck(SpendTracker):
        def __init__(self, n: int) -> None:
            super().__init__(per_case_cap=1e9, per_run_cap=1e9)
            self.n, self.seen = n, 0

        def check(self, case_id: str) -> None:
            self.seen += 1
            if self.seen >= self.n:
                raise BudgetExceeded("spend_cap", "simulated breach at finalize")

    # hypothesis (1) and orchestrator (2) succeed; finalize (3) breaches.
    guards = RunGuards(
        bucket=TokenBucket(rate_per_minute=6000),
        daily=DailyRequestCounter(tmp_path / "daily.json", limit=10_000),
        spend=BreachOnNthCheck(3),
    )
    graph, _, store = build(cases, [HYP, decide("finalize")], guards=guards, max_turns=10)
    out = await run(graph, store)
    assert out["final"] is not None
    assert out["final"].abstain is False, "a differential existed, so no abstention"
    assert out["final"].diagnosis == "Endometritis"
    assert "Forced finalize" in out["final"].rationale


async def test_an_unavailable_test_is_marked_in_the_summary_the_orchestrator_reads(cases):
    """The orchestrator must be able to tell a refused order from a fulfilled one.

    `tests_ordered` lists the doctor's own request strings. Unmarked, a test the
    case does not contain reads exactly like one that returned a result, so the
    orchestrator re-orders what it cannot have -- observed live on medqa-0002,
    where the doctor spent 2 of 8 turns re-asking for a CSF JC virus PCR.

    The refusal *text* alone does not fix this: it reaches the orchestrator only
    if the hypothesis model chooses to paraphrase it into `findings`, and in the
    observed run it did not.
    """
    script = [HYP, decide("order_test", "CSF JC virus PCR"),
              HYP, decide("order_test", "MRI brain with contrast"),
              HYP, decide("finalize"), FINAL]
    graph, model, store = build(cases, script, case_id="medqa-0002", max_turns=6)
    await graph.ainvoke(
        new_state("medqa-0002", store.doctor_view("medqa-0002").objective_for_doctor),
        {"recursion_limit": 100})

    prompts = [p for p in model.rendered_prompts if "Choose exactly one action" in p]
    after = [p for p in prompts if "CSF JC virus PCR" in p]
    assert after, "the request never reached the summary at all"
    line = [ln for ln in after[0].splitlines() if "CSF JC virus PCR" in ln][0]
    assert "NOT IN THIS CASE" in line, (
        "an unavailable test is indistinguishable from a fulfilled one"
    )
    # The test that *did* resolve must not be marked.
    got = [p for p in prompts if "MRI brain with contrast" in p]
    assert got, "the fulfilled request never reached the summary"
    mri_line = [ln for ln in got[-1].splitlines() if "MRI brain with contrast" in ln][0]
    assert "NOT IN THIS CASE" not in mri_line and "result received" in mri_line, mri_line
