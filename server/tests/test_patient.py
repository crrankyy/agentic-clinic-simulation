"""Patient prompt construction — a visibility test as much as a rendering one."""

from __future__ import annotations

from agentclinic.agents.patient import build_prompt
from agentclinic.data.views import CaseStore


def test_prompt_contains_only_patient_side_information(cases):
    """The patient must not see the objective, the exam findings or the tests."""
    store = CaseStore(cases)
    for case in cases[:40]:
        prompt = build_prompt(store.patient_view(case.case_id), "What brings you in?").casefold()
        assert case.correct_diagnosis.casefold() not in prompt
        assert case.objective_for_doctor.casefold() not in prompt
        # Test *result text* must never appear. Key names are not asserted on:
        # a key like "Blood_Tests" is ordinary English that can legitimately
        # occur in a patient history, so asserting on it would be flaky rather
        # than meaningful.
        for value in case.test_results.values():
            if isinstance(value, str) and len(value) > 25:
                assert value.casefold() not in prompt


def test_prompt_renders_dict_and_list_fields_readably(cases):
    """Review_of_Systems is a dict in 10 cases; str() would emit Python repr."""
    store = CaseStore(cases)
    dict_case = next(c for c in cases if isinstance(c.patient_actor.review_of_systems, dict))
    prompt = build_prompt(store.patient_view(dict_case.case_id), "How are you?")
    assert "{'" not in prompt and "':" not in prompt.replace("':", "")


def test_undocumented_keys_reach_the_patient(cases):
    """A patient on medications must be able to say so (brief §5.1)."""
    store = CaseStore(cases)
    case = next(c for c in cases if c.line_number == 18)
    prompt = build_prompt(store.patient_view(case.case_id), "Any medicines?")
    assert "Current Medications" in prompt


def test_missing_fields_render_as_not_recorded(cases):
    store = CaseStore(cases)
    case = next(c for c in cases if c.line_number == 120)  # no Past_Medical_History
    assert "Past medical history: not recorded" in build_prompt(
        store.patient_view(case.case_id), "hi")


def test_empty_symptoms_case_renders(cases):
    store = CaseStore(cases)
    case = next(c for c in cases if c.line_number == 132)  # Symptoms == {}
    prompt = build_prompt(store.patient_view(case.case_id), "hi")
    assert "Main problem: not recorded" in prompt
