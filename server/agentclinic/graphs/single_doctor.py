"""The single-doctor encounter graph.

    START → brief → hypothesis → solo_decide → absorb_panel → route_action
              ↑                                                    ↓
              └──────────── route_stop ← check_stop ← action ──────┘
                                  ↓
                               finalize → END

Deliberately shaped so the panel graph differs by exactly two nodes. `hypothesis`
sits in the parent of both, and the orchestrator sits in a subgraph whose
`input_schema` omits `encounter_log` — which is what stops the orchestrator
reading raw event text and makes "the doctor works from a summary" a structural
property rather than prompt discipline.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents.doctor import make_finalize_node, make_hypothesis_node, make_orchestrator_node
from .nodes import (
    absorb_panel,
    make_ask_patient,
    make_check_stop,
    make_gatekeeper_node,
    make_search_literature,
)
from .routing import route_action, route_stop
from .state import DifferentialItem, EncounterState, EncounterSummary, Event, RedFlag


class DecideInput(TypedDict, total=False):
    """What a deliberation may see. `encounter_log` is absent on purpose."""

    objective: str
    summary: EncounterSummary
    differential: list[DifferentialItem]
    red_flags: list[RedFlag]
    turn: int
    challenged_this_finalize: bool
    budget_exhausted: bool
    challenger_opinion: Any
    cost_objection: Any


class DecideOutput(TypedDict, total=False):
    """What a deliberation may return. `encounter_log` is absent on purpose."""

    action: str | None
    action_argument: str | None
    panel_events: list[Event]
    challenger_opinion: Any
    cost_objection: Any
    budget_exhausted: bool
    parse_failures: int
    stop_reason: str | None
    # `spend_usd` is deliberately NOT here: D-039 makes the client the
    # authoritative total and `check_stop` its only writer in the parent.
    # Letting the subgraph write it too would give the key two writers and
    # break the single-writer property the allow-list test relies on.


def build_solo_decide(orchestrator: Callable) -> Any:
    """One-node decision subgraph, declared with the same schemas as the panel."""
    sub = StateGraph(EncounterState, input_schema=DecideInput, output_schema=DecideOutput)
    sub.add_node("orchestrator", orchestrator)
    sub.add_edge(START, "orchestrator")
    sub.add_edge("orchestrator", END)
    return sub.compile()


def build_single_doctor_graph(
    *,
    caller: Any,
    patient: Any,
    gatekeeper: Any,
    case_id: str,
    decision_model: type,
    enabled: frozenset[str],
    max_turns: int,
    guards: Any = None,
    evidence: Any = None,
    checkpointer: Any = None,
    config_dir: Any = None,
) -> Any:
    """Compile the encounter graph for one case.

    Everything case-related is bound **by closure**, never passed through
    `config` — LangGraph records config in checkpoint metadata, and the brief
    forbids case data reaching a checkpoint.
    """
    hypothesis = make_hypothesis_node(caller, case_id, config_dir)
    orchestrator = make_orchestrator_node(caller, case_id, decision_model, max_turns, config_dir)
    finalize = make_finalize_node(caller, case_id, config_dir)

    graph = StateGraph(EncounterState)
    graph.add_node("brief", lambda state: {})
    graph.add_node("hypothesis", hypothesis)
    graph.add_node("solo_decide", build_solo_decide(orchestrator))
    graph.add_node("absorb_panel", absorb_panel)
    graph.add_node("ask_patient", make_ask_patient(patient, case_id))
    graph.add_node("request_exam", make_gatekeeper_node(gatekeeper, "exams", "exam"))
    graph.add_node("order_test", make_gatekeeper_node(gatekeeper, "tests", "test"))
    if "search_literature" in enabled:
        graph.add_node("search_literature", make_search_literature(evidence, case_id))
    graph.add_node("check_stop", make_check_stop(max_turns, guards))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "hypothesis")
    graph.add_edge("hypothesis", "solo_decide")
    graph.add_edge("solo_decide", "absorb_panel")
    graph.add_conditional_edges(
        "absorb_panel",
        lambda s: route_action(s, enabled=enabled, has_challenger=False),
        {**{a: a for a in enabled}, "finalize": "finalize"},
    )
    for action in enabled:
        graph.add_edge(action, "check_stop")
    graph.add_conditional_edges(
        "check_stop", route_stop, {"continue": "hypothesis", "stop": "finalize"}
    )
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
