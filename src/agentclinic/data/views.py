"""Restricted views over a case — the mechanism that enforces isolation.

This module is the whole of the brief's "information isolation must be enforced
by the state design, not by prompts" (§5), so it is deliberately boring:

* `CaseStore` holds `Case` objects **outside** LangGraph state and exposes **no
  method that returns one**. The only way out is through a view.
* Each view is a frozen dataclass carrying a disjoint slice. `Correct_Diagnosis`
  and `Management_and_Follow_Up` appear on `JudgeView` and nowhere else (D-016).
* Views are bound into nodes **by closure** at graph-construction time, never
  passed through `config["configurable"]` — LangGraph records config in
  checkpoint metadata, and the brief forbids ground truth reaching a checkpoint.

The guarantee this buys, stated exactly: *no `EncounterState` key is ever
written from `Correct_Diagnosis` or `Management_and_Follow_Up`, because no
doctor-side view exposes them.* It is deliberately narrower than "the diagnosis
never appears in state", which is false for 29 cases and unachievable — see
`leakage.py` and PLAN.md §3.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Case


@dataclass(frozen=True, slots=True)
class DoctorView:
    """Everything a doctor-side node may read from the case: the brief, only."""

    case_id: str
    objective_for_doctor: str


@dataclass(frozen=True, slots=True)
class PatientView:
    """`Patient_Actor` only, including its undocumented keys (D-018)."""

    demographics: str
    history: str
    primary_symptom: str | None
    secondary_symptoms: tuple[str, ...]
    past_medical_history: Any
    social_history: Any
    review_of_systems: Any
    extra_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GatekeeperView:
    """Exam findings and test results. Returned only in matched fragments."""

    physical_examination_findings: dict[str, Any]
    test_results: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JudgeView:
    """Ground truth. Constructed by the eval runner *after* the encounter ends."""

    correct_diagnosis: str
    management_and_follow_up: Any


@dataclass(frozen=True, slots=True)
class CaseMetadata:
    """Bookkeeping the eval runner needs that is not ground truth."""

    case_id: str
    line_number: int
    dx_in_results: bool
    dx_tokens_in_results: bool
    has_test_results: bool


class CaseStore:
    """Holds cases outside graph state and hands out narrow views."""

    def __init__(self, cases: list[Case]) -> None:
        self._cases: dict[str, Case] = {c.case_id: c for c in cases}

    def __len__(self) -> int:
        return len(self._cases)

    def case_ids(self) -> list[str]:
        return sorted(self._cases)

    def _get(self, case_id: str) -> Case:
        try:
            return self._cases[case_id]
        except KeyError:
            raise KeyError(f"unknown case_id {case_id!r}") from None

    # --- views ------------------------------------------------------------
    def doctor_view(self, case_id: str) -> DoctorView:
        c = self._get(case_id)
        return DoctorView(case_id=c.case_id, objective_for_doctor=c.objective_for_doctor)

    def patient_view(self, case_id: str) -> PatientView:
        pa = self._get(case_id).patient_actor
        return PatientView(
            demographics=pa.demographics,
            history=pa.history,
            primary_symptom=pa.symptoms.primary_symptom,
            secondary_symptoms=tuple(pa.symptoms.secondary_symptoms),
            past_medical_history=pa.past_medical_history,
            social_history=pa.social_history,
            review_of_systems=pa.review_of_systems,
            extra_fields=pa.extra_fields,
        )

    def gatekeeper_view(self, case_id: str) -> GatekeeperView:
        c = self._get(case_id)
        return GatekeeperView(
            physical_examination_findings=c.physical_examination_findings,
            test_results=c.test_results,
        )

    def judge_view(self, case_id: str) -> JudgeView:
        c = self._get(case_id)
        return JudgeView(
            correct_diagnosis=c.correct_diagnosis,
            management_and_follow_up=c.management_and_follow_up,
        )

    def metadata(self, case_id: str) -> CaseMetadata:
        c = self._get(case_id)
        return CaseMetadata(
            case_id=c.case_id,
            line_number=c.line_number,
            dx_in_results=c.dx_in_results,
            dx_tokens_in_results=c.dx_tokens_in_results,
            has_test_results=c.has_test_results,
        )
