"""Typed models for one OSCE case.

Design notes that are not obvious from the shapes:

* The nested exam and test dictionaries stay **generic** (brief §3). Their keys
  vary per case — 234 distinct top-level `Test_Results` keys across 214 cases,
  165 of them appearing exactly once — so there is nothing stable to model.
* Values are `str | dict | list`, not uniformly dicts. 16 cases have string
  values at the top level of `Test_Results`, 2 contain lists, and
  `Physical_Examination_Findings` contains lists in 2 more. The same is true of
  `Patient_Actor`: `Review_of_Systems` is a dict in 10 cases and
  `Past_Medical_History` is a list or dict in 3.
* Undocumented `Patient_Actor` keys (`Current_Medications`, `Medications`,
  `Drug_History`, `Family_History`) are **preserved**, not dropped (D-018): a
  patient who is on medications must not answer "I am not on any medications",
  which would be a fabricated negative that brief §5.1 forbids.
* `Correct_Diagnosis` and `Management_and_Follow_Up` live here because this
  object never enters graph state — it is held by the `CaseStore` and reaches
  nodes only through narrow views (PLAN.md §1.1).
"""

from __future__ import annotations

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

#: A value of arbitrary shape inside the exam/test trees.
NestedValue: TypeAlias = Any

_CFG = ConfigDict(populate_by_name=True, extra="allow", frozen=True)


class Symptoms(BaseModel):
    """Presenting symptoms. Empty in one case (132), hence both fields optional."""

    model_config = _CFG

    primary_symptom: str | None = Field(default=None, alias="Primary_Symptom")
    secondary_symptoms: list[str] = Field(default_factory=list, alias="Secondary_Symptoms")


class PatientActor(BaseModel):
    """What the patient agent may see — and the only thing it may see."""

    model_config = _CFG

    demographics: str = Field(alias="Demographics")
    history: str = Field(alias="History")
    symptoms: Symptoms = Field(default_factory=Symptoms, alias="Symptoms")
    # str in most cases, dict or list in a handful; missing entirely in case 120.
    past_medical_history: NestedValue = Field(default=None, alias="Past_Medical_History")
    social_history: NestedValue = Field(default=None, alias="Social_History")
    review_of_systems: NestedValue = Field(default=None, alias="Review_of_Systems")

    @property
    def extra_fields(self) -> dict[str, NestedValue]:
        """Undocumented patient-side keys, preserved verbatim (D-018)."""
        return dict(self.model_extra or {})


class Case(BaseModel):
    """One complete OSCE case, including ground truth.

    Never placed in LangGraph state. Held by `CaseStore`; nodes receive views.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    case_id: str
    line_number: int

    objective_for_doctor: str
    patient_actor: PatientActor
    physical_examination_findings: dict[str, NestedValue] = Field(default_factory=dict)
    test_results: dict[str, NestedValue] = Field(default_factory=dict)

    # --- hidden from every doctor-side view (D-016) -------------------------
    correct_diagnosis: str
    management_and_follow_up: NestedValue = None

    # --- derived leakage flags, computed at load (PLAN.md §3.2) -------------
    dx_in_results: bool = False
    dx_tokens_in_results: bool = False

    @property
    def has_test_results(self) -> bool:
        """False for the 4 cases whose `Test_Results` is empty (69, 106, 111, 209)."""
        return bool(self.test_results)
