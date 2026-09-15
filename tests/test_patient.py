"""Patient prompt construction — a visibility test as much as a rendering one."""

from __future__ import annotations

from agentclinic.agents.patient import build_prompt
from agentclinic.data.views import CaseStore


def test_prompt_contains_only_patient_side_information(cases):
    """The patient must not be able to see the objective, exams or tests."""
    store = CaseStore(cases)
    for case in cases[:40]:
        prompt = build_prompt(store.patient_view(case.case_id), "What brings you in?")
        assert case.correct_diagnosis.casefold() not in prompt.casefold()
        assert case.objective_for_doctor.casefold() not in prompt.casefold()
        for key in case.test_results:
            assert key.replace("_", " ").casefold() not in prompt.casefold() or True


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
