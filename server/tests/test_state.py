"""The allow-list isolation test (Phase 2 half of PLAN.md §3.3 item 3).

Every `EncounterState` key must have a declared writer. This is the test that
catches someone adding a channel for ground truth: a new key with no entry in
`STATE_SOURCES` fails here, and an entry naming a hidden case field fails too.
"""

from __future__ import annotations

from agentclinic.graphs.state import (
    FORBIDDEN_SOURCES,
    STATE_SOURCES,
    EncounterState,
    new_state,
)


def test_every_state_key_has_a_declared_writer():
    assert set(EncounterState.__annotations__) == set(STATE_SOURCES)


def test_no_writer_is_a_hidden_case_field():
    """`Correct_Diagnosis` and `Management_and_Follow_Up` may never be a source."""
    for key, sources in STATE_SOURCES.items():
        for source in sources:
            assert source not in FORBIDDEN_SOURCES, f"{key} is written from {source}"


def test_adding_an_undeclared_key_would_fail():
    """Guards the guard: the test is only useful if it can actually fail."""
    declared = set(STATE_SOURCES)
    pretend = set(EncounterState.__annotations__) | {"correct_diagnosis"}
    assert pretend != declared


def test_initial_state_contains_no_ground_truth(cases):
    """The objective is the only case text in a fresh state."""
    case = next(c for c in cases if c.case_id == "medqa-0002")
    state = new_state(case.case_id, case.objective_for_doctor)
    blob = repr(state).casefold()
    assert case.correct_diagnosis.casefold() not in blob


def test_encounter_log_starts_with_only_the_objective():
    state = new_state("c", "Assess the patient.")
    assert [e.kind for e in state["encounter_log"]] == ["objective"]
