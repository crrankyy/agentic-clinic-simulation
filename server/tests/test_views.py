"""The Phase 1 isolation test — the project's central guarantee.

Asserts the *provable* property (PLAN.md §1.1): no doctor-side view exposes
`Correct_Diagnosis` or `Management_and_Follow_Up`, so no state key can ever be
written from them.

Deliberately NOT asserted here: that the diagnosis string never appears anywhere
a doctor can see. That is **false** for 29 cases, where the dataset embeds the
answer in test results the gatekeeper is designed to return — see
`test_leakage.py` and PLAN.md §3.2. The string-scan test over the leak-free
subset lands in Phase 3, when a full scripted encounter exists.
"""

from __future__ import annotations

import dataclasses
import json
import inspect

from agentclinic.data.leakage import (
    flatten_keys_and_values,
    normalise,
    strip_trailing_parenthetical,
)
from agentclinic.data.models import Case
from agentclinic.data.views import (
    CaseStore,
    DoctorView,
    GatekeeperView,
    JudgeView,
    PatientView,
)

HIDDEN = ("correct_diagnosis", "management_and_follow_up")
DOCTOR_SIDE = (DoctorView, PatientView, GatekeeperView)


def render(view) -> str:
    """Flatten a view to searchable text.

    `ensure_ascii=False` matters: with the default, `json.dumps` escapes a curly
    apostrophe to `\\u2019`, so a needle like "Hirschsprung\u2019s disease"
    would never be found and this test would pass **falsely**. Several
    diagnoses in the dataset contain non-ASCII characters.
    """
    return json.dumps(dataclasses.asdict(view), default=str, ensure_ascii=False).casefold()


# --- structural: the views themselves ---------------------------------------

def test_no_doctor_side_view_declares_a_hidden_field():
    for view_cls in DOCTOR_SIDE:
        names = {f.name for f in dataclasses.fields(view_cls)}
        assert not names & set(HIDDEN), f"{view_cls.__name__} exposes ground truth"


def test_judge_view_is_the_only_carrier_of_ground_truth():
    names = {f.name for f in dataclasses.fields(JudgeView)}
    assert set(HIDDEN) <= names


def test_case_store_exposes_no_method_returning_a_full_case():
    """The only way data leaves the store is through a view."""
    for name, member in inspect.getmembers(CaseStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        returns = inspect.signature(member).return_annotation
        assert returns is not Case and returns != "Case", (
            f"CaseStore.{name} returns a full Case, bypassing the view boundary"
        )


# --- behavioural: over every real case --------------------------------------

def test_ground_truth_never_reaches_a_doctor_side_view(cases):
    store = CaseStore(cases)
    for case in cases:
        dx = case.correct_diagnosis.casefold()
        assert dx not in render(store.doctor_view(case.case_id))
        assert dx not in render(store.patient_view(case.case_id))


def test_management_and_follow_up_never_reaches_a_doctor_side_view(cases):
    store = CaseStore(cases)
    case = next(c for c in cases if c.management_and_follow_up)
    assert case.line_number == 133
    needle = "pediatric endocrinology"
    for view in (store.doctor_view(case.case_id),
                 store.patient_view(case.case_id),
                 store.gatekeeper_view(case.case_id)):
        assert needle not in render(view)
    assert needle in render(store.judge_view(case.case_id))


def test_gatekeeper_view_does_carry_the_diagnosis_for_flagged_cases(cases):
    """Positive test: if this stops holding, `dx_in_results` has drifted.

    The flagged cases are *expected* to leak — that is the measured quantity.
    Asserting it keeps a future change to the rule from passing silently.
    """
    store = CaseStore(cases)
    flagged = [c for c in cases if c.dx_in_results]
    assert len(flagged) == 29
    for case in flagged:
        from agentclinic.data.leakage import normalise, strip_trailing_parenthetical
        needle = normalise(strip_trailing_parenthetical(case.correct_diagnosis))
        view = store.gatekeeper_view(case.case_id)
        # Use the production flattener, not json.dumps, so the test exercises
        # the same code path the flag itself uses.
        blob = normalise(" ".join(flatten_keys_and_values(
            view.test_results, view.physical_examination_findings)))
        assert needle in blob, f"{case.case_id} is flagged but the view does not carry the diagnosis"


def test_patient_view_carries_undocumented_keys(cases):
    store = CaseStore(cases)
    case = next(c for c in cases if c.line_number == 18)
    assert "Current_Medications" in store.patient_view(case.case_id).extra_fields


def test_views_are_frozen(cases):
    store = CaseStore(cases)
    view = store.doctor_view("medqa-0001")
    try:
        view.objective_for_doctor = "tampered"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("views must be immutable")
