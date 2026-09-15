"""Helpers for building a fake-model single-doctor graph in tests."""

from __future__ import annotations

from typing import Any

from agentclinic.agents.gatekeeper import Gatekeeper
from agentclinic.agents.patient import PatientReply
from agentclinic.config import load_test_costs
from agentclinic.data.views import CaseStore
from agentclinic.graphs.schemas import make_orchestrator_decision
from agentclinic.graphs.single_doctor import build_single_doctor_graph
from agentclinic.llm.fake import FakeChatModel
from agentclinic.llm.openrouter import LLMCaller

ENABLED = frozenset({"ask_patient", "request_exam", "order_test"})
Decision = make_orchestrator_decision(ENABLED)

HYP = {
    "findings": ["postpartum fever", "abdominal tenderness"],
    "differential": [{"diagnosis": "Endometritis", "probability": 0.5, "rationale": "fever"}],
    "ruled_out": [], "open_questions": ["timing?"], "red_flags": [],
}
FINAL = {
    "diagnosis": "Endometritis", "differential": HYP["differential"],
    "confidence": 0.7, "abstain": False, "red_flag": [], "rationale": "postpartum fever",
}


def decide(action: str, argument: str = "") -> dict[str, Any]:
    return {"action": action, "argument": argument, "reason": "r", "expected_information": "i"}


class ScriptedPatient:
    def __init__(self, reply: str = "Two days ago.", unknown: bool = False) -> None:
        self.reply, self.unknown, self.asked = reply, unknown, []

    async def answer(self, question: str, *, case_id: str) -> PatientReply:
        self.asked.append(question)
        return PatientReply(reply=self.reply, unknown=self.unknown)


def build(cases, script, *, case_id="medqa-0010", max_turns=10, guards=None, patient=None):
    store = CaseStore(cases)
    model = FakeChatModel(script)
    caller = LLMCaller(model, guards=guards)
    graph = build_single_doctor_graph(
        caller=caller,
        patient=patient or ScriptedPatient(),
        gatekeeper=Gatekeeper(store.gatekeeper_view(case_id), load_test_costs()),
        case_id=case_id, decision_model=Decision, enabled=ENABLED,
        max_turns=max_turns, guards=guards,
    )
    return graph, model, store
