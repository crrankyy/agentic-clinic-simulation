"""The human-driven graph: the real state machine, driven by hand."""

from __future__ import annotations

import pytest
from langgraph.types import Command

from agentclinic.agents.gatekeeper import Gatekeeper
from agentclinic.agents.patient import PatientReply
from agentclinic.config import load_test_costs
from agentclinic.data.views import CaseStore
from agentclinic.graphs.interactive import build_interactive_graph
from agentclinic.graphs.state import new_state


class ScriptedPatient:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def answer(self, question: str, *, case_id: str, history=()) -> PatientReply:
        self.asked.append(question)
        return PatientReply(reply=f"answer to {question}", unknown=False)


def build(cases, case_id="medqa-0010", max_turns=5):
    store = CaseStore(cases)
    patient = ScriptedPatient()
    graph = build_interactive_graph(
        patient=patient,
        gatekeeper=Gatekeeper(store.gatekeeper_view(case_id), load_test_costs()),
        max_turns=max_turns,
        case_id=case_id,
    )
    return graph, patient, store.doctor_view(case_id)


async def drive(graph, view, case_id, actions, thread="t"):
    cfg = {"configurable": {"thread_id": thread}}
    await graph.ainvoke(new_state(case_id, view.objective_for_doctor), cfg)
    for action, argument in actions:
        await graph.ainvoke(Command(resume={"action": action, "argument": argument}), cfg)
    return (await graph.aget_state(cfg)).values


async def test_interrupt_suspends_before_any_action(cases):
    graph, patient, view = build(cases)
    cfg = {"configurable": {"thread_id": "a"}}
    out = await graph.ainvoke(new_state("medqa-0010", view.objective_for_doctor), cfg)
    assert "__interrupt__" in out
    assert patient.asked == [], "nothing may run before the human decides"


async def test_each_action_advances_exactly_one_turn(cases):
    graph, _, view = build(cases)
    s = await drive(graph, view, "medqa-0010",
                    [("ask_patient", "q1"), ("order_test", "CBC")], thread="b")
    assert s["turn"] == 2


async def test_turn_cap_stops_the_encounter(cases):
    graph, _, view = build(cases, max_turns=2)
    s = await drive(graph, view, "medqa-0010",
                    [("ask_patient", "q1"), ("ask_patient", "q2")], thread="c")
    assert s["stop_reason"] == "turn_cap"


async def test_encounter_log_is_not_duplicated(cases):
    """The delta-channel rule: nodes return only new events."""
    graph, _, view = build(cases)
    s = await drive(graph, view, "medqa-0010",
                    [("ask_patient", "q1"), ("ask_patient", "q2")], thread="d")
    texts = [e.text for e in s["encounter_log"]]
    assert len(texts) == len(set(texts)), f"duplicated events: {texts}"


async def test_test_costs_accumulate(cases):
    graph, _, view = build(cases)
    s = await drive(graph, view, "medqa-0010",
                    [("order_test", "CBC"), ("order_test", "no such test")], thread="e")
    costs = load_test_costs()
    assert s["test_cost_usd"] == costs.price("Complete_Blood_Count") + costs.unknown_price


async def test_unlisted_request_is_logged_as_an_event(cases):
    graph, _, view = build(cases)
    s = await drive(graph, view, "medqa-0010", [("order_test", "bone scan")], thread="f")
    assert any(e.kind == "unlisted_test" for e in s["encounter_log"])


async def test_ground_truth_never_enters_state_for_a_leak_free_case(cases):
    """The string-scan property, on a case known not to embed its diagnosis."""
    case = next(c for c in cases if not c.dx_in_results and c.test_results)
    graph, _, view = build(cases, case_id=case.case_id)
    s = await drive(graph, view, case.case_id,
                    [("ask_patient", "what happened?"),
                     ("order_test", next(iter(case.test_results)))], thread="g")
    assert case.correct_diagnosis.casefold() not in repr(s).casefold()


async def test_checkpoint_of_a_leak_free_case_holds_no_ground_truth(cases):
    """Brief §11 requires the checkpoint itself to be clean."""
    case = next(c for c in cases if not c.dx_in_results and c.test_results)
    graph, _, view = build(cases, case_id=case.case_id)
    cfg = {"configurable": {"thread_id": "h"}}
    await graph.ainvoke(new_state(case.case_id, view.objective_for_doctor), cfg)
    await graph.ainvoke(Command(resume={"action": "ask_patient", "argument": "hi"}), cfg)
    snapshot = await graph.aget_state(cfg)
    assert case.correct_diagnosis.casefold() not in repr(snapshot).casefold()
