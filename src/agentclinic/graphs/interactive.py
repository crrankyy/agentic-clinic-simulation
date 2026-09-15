"""The human-driven encounter graph — you play the doctor.

This is Phase 2's deliverable and it is not a toy. It is the *real* state
machine: the same `EncounterState`, the same `route_action`, the same
`check_stop`, the same patient and gatekeeper nodes that Phase 3 will drive with
a model. Only the orchestrator differs — it calls `interrupt()` and waits for
you instead of calling an LLM.

Driving the real graph by hand is the cheapest way to find design faults before
LLM non-determinism starts hiding them (Q-31, D-032).
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents.gatekeeper import Gatekeeper
from .nodes import make_ask_patient, make_check_stop, make_gatekeeper_node
from .routing import route_action, route_stop
from .state import EncounterState, Event

#: Actions a human doctor can take in Phase 2. `search_literature` arrives with
#: the evidence agent in Phase 5; per PLAN.md §4.1 the action set is built per
#: run, so it is simply absent here rather than present-but-broken.
INTERACTIVE_ACTIONS = frozenset({"ask_patient", "request_exam", "order_test"})


def build_interactive_graph(
    *,
    patient: Any,
    gatekeeper: Gatekeeper,
    max_turns: int,
    case_id: str,
    enabled: frozenset[str] = INTERACTIVE_ACTIONS,
) -> Any:
    """Compile the human-driven graph.

    `patient` and `gatekeeper` are bound here **by closure**, which is the
    isolation mechanism: no node holds a reference to any view but its own, and
    nothing case-related travels through `config`, which LangGraph would record
    in checkpoint metadata.
    """

    def brief(state: EncounterState) -> dict[str, Any]:
        return {}  # `new_state` already seeded the objective

    def human_orchestrator(state: EncounterState) -> dict[str, Any]:
        """Hand control to the human and wait for their chosen action."""
        decision = interrupt(
            {
                "turn": state.get("turn", 0),
                "objective": state.get("objective", ""),
                "log": [e.model_dump() for e in state.get("encounter_log", [])],
            }
        )
        return {
            "action": decision.get("action"),
            "action_argument": decision.get("argument", ""),
        }

    stop_node = make_check_stop(max_turns)

    def finalize(state: EncounterState) -> dict[str, Any]:
        turn = int(state.get("turn", 0))
        update: dict[str, Any] = {
            "encounter_log": [Event(turn=turn, kind="stop", actor="system",
                                    text="encounter finished")],
        }
        if not state.get("stop_reason"):
            update["stop_reason"] = "finalize"
        return update

    graph = StateGraph(EncounterState)
    graph.add_node("brief", brief)
    graph.add_node("orchestrator", human_orchestrator)
    graph.add_node("ask_patient", make_ask_patient(patient, case_id))
    graph.add_node("request_exam", make_gatekeeper_node(gatekeeper, "exams", "exam"))
    graph.add_node("order_test", make_gatekeeper_node(gatekeeper, "tests", "test"))
    graph.add_node("check_stop", stop_node)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        lambda s: route_action(s, enabled=enabled, has_challenger=False),
        {**{a: a for a in enabled}, "finalize": "finalize"},
    )
    for action in enabled:
        graph.add_edge(action, "check_stop")
    graph.add_conditional_edges(
        "check_stop", route_stop, {"continue": "orchestrator", "stop": "finalize"}
    )
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=MemorySaver())
