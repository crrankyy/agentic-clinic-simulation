"""The panel encounter graph.

Structurally identical to `single_doctor` except that the decision subgraph is
the panel rather than a lone orchestrator, and a voluntary finalize passes
through `challenger_final` once. Keeping the two graphs otherwise identical is
what makes the comparison mean "does a doctor receiving challenge and cost
opinions decide differently" rather than confounding sub-roles with loop shape.

A **cap-forced** finalize does not run the challenger (D-038). The node that
used to sit there produced an opinion nothing read: `finalize` works from the
differential and summary, so an opinion arriving after the budget is spent could
not change any output.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..agents.doctor import (
    make_challenger_final_node,
    make_challenger_node,
    make_cost_steward_node,
    make_finalize_node,
    make_hypothesis_node,
    make_orchestrator_node,
)
from .doctor_panel import build_doctor_panel
from .nodes import (
    absorb_panel,
    make_ask_patient,
    make_check_stop,
    make_gatekeeper_node,
    make_search_literature,
)
from .routing import route_action, route_stop
from .state import EncounterState


def build_encounter_graph(
    *,
    caller: Any,
    patient: Any,
    gatekeeper: Any,
    costs: Any,
    case_id: str,
    decision_model: type,
    enabled: frozenset[str],
    max_turns: int,
    guards: Any = None,
    evidence: Any = None,
    checkpointer: Any = None,
    config_dir: Any = None,
) -> Any:
    """Compile the panel encounter graph for one case."""
    hypothesis = make_hypothesis_node(caller, case_id, config_dir)
    orchestrator = make_orchestrator_node(caller, case_id, decision_model, max_turns, config_dir)
    challenger = make_challenger_node(caller, case_id, channel="panel_events",
                                      config_dir=config_dir)
    cost_steward = make_cost_steward_node(caller, case_id, costs, config_dir)
    challenger_final = make_challenger_final_node(caller, case_id, config_dir)
    finalize = make_finalize_node(caller, case_id, config_dir)

    panel = build_doctor_panel(orchestrator=orchestrator, challenger=challenger,
                               cost_steward=cost_steward)

    graph = StateGraph(EncounterState)
    graph.add_node("brief", lambda state: {})
    graph.add_node("hypothesis", hypothesis)
    graph.add_node("panel", panel)
    graph.add_node("absorb_panel", absorb_panel)
    graph.add_node("challenger_final", challenger_final)
    graph.add_node("ask_patient", make_ask_patient(patient, case_id))
    graph.add_node("request_exam", make_gatekeeper_node(gatekeeper, "exams", "exam"))
    graph.add_node("order_test", make_gatekeeper_node(gatekeeper, "tests", "test"))
    if "search_literature" in enabled:
        graph.add_node("search_literature", make_search_literature(evidence, case_id))
    graph.add_node("check_stop", make_check_stop(max_turns, guards))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "hypothesis")
    graph.add_edge("hypothesis", "panel")
    graph.add_edge("panel", "absorb_panel")
    graph.add_conditional_edges(
        "absorb_panel",
        lambda s: route_action(s, enabled=enabled, has_challenger=True),
        {**{a: a for a in enabled}, "finalize": "finalize",
         "challenger_final": "challenger_final"},
    )
    # One edge only. The bound on re-deliberation lives in `route_action`, which
    # reads `challenged_this_finalize` *before* this node sets it.
    graph.add_edge("challenger_final", "hypothesis")
    for action in enabled:
        graph.add_edge(action, "check_stop")
    graph.add_conditional_edges(
        "check_stop", route_stop, {"continue": "hypothesis", "stop": "finalize"}
    )
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
