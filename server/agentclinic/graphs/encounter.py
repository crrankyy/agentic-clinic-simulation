"""The encounter graph, for the single doctor and the panel alike.

    START → hypothesis → decide → absorb_panel → route_action
              ↑                                       ↓
              └────── route_stop ← check_stop ← action ┘
                          ↓ (turn cap: hypothesis_final first)
                       finalize → END

`hypothesis` sits in the parent; the orchestrator sits in the `decide`
subgraph, whose `input_schema` omits `encounter_log` — which is what stops the
orchestrator reading raw event text and makes "the doctor works from a
summary" a structural property rather than prompt discipline.

The panel differs in exactly two places: `decide` is the doctor panel rather
than a lone orchestrator, and a voluntary finalize passes through
`challenger_final` once. Keeping the loop otherwise identical is what makes the
comparison mean "does a doctor receiving challenge and cost opinions decide
differently" rather than confounding sub-roles with loop shape. A cap-forced
finalize does not run the challenger (D-038): an opinion arriving after the
budget is spent could not change any output.
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
from ..agents.gatekeeper import Gatekeeper, make_llm_disambiguator
from ..agents.patient import Patient
from .doctor_panel import build_doctor_panel, build_solo_decide
from .ledger import QUESTION_OVERLAP
from .nodes import absorb_panel, make_ask_patient, make_check_stop, make_gatekeeper_node
from .routing import route_action, route_stop
from .schemas import make_orchestrator_decision
from .state import EncounterState

ENABLED_ACTIONS = frozenset({"ask_patient", "request_exam", "order_test"})


def build_graph(
    *,
    caller: Any,
    patient: Any,
    gatekeeper: Any,
    case_id: str,
    decision_model: type,
    enabled: frozenset[str],
    max_turns: int,
    guards: Any = None,
    question_overlap: float = QUESTION_OVERLAP,
    panel: bool = False,
    costs: Any = None,
) -> Any:
    """Compile the encounter graph for one case. `panel` needs `costs`.

    Everything case-related is bound **by closure**, never passed through
    `config` — LangGraph records config in checkpoint metadata, and the brief
    forbids case data reaching a checkpoint.
    """
    orchestrator = make_orchestrator_node(
        caller, case_id, decision_model, max_turns, enabled=enabled,
        resolve=getattr(gatekeeper, "resolve_deterministic", None),
        question_overlap=question_overlap,
    )
    decide = (build_doctor_panel(
        orchestrator=orchestrator,
        challenger=make_challenger_node(caller, case_id, channel="panel_events"),
        cost_steward=make_cost_steward_node(caller, case_id, costs),
    ) if panel else build_solo_decide(orchestrator))

    graph = StateGraph(EncounterState)
    graph.add_node("hypothesis", make_hypothesis_node(caller, case_id))
    # M-07: one more read of the evidence before a turn-cap finalize, so the
    # last action's result is in the summary the final answer is built from.
    graph.add_node("hypothesis_final", make_hypothesis_node(caller, case_id))
    graph.add_node("decide", decide)
    graph.add_node("absorb_panel", absorb_panel)
    graph.add_node("ask_patient", make_ask_patient(patient, case_id))
    graph.add_node("request_exam", make_gatekeeper_node(gatekeeper, "exams", "exam"))
    graph.add_node("order_test", make_gatekeeper_node(gatekeeper, "tests", "test"))
    graph.add_node("check_stop", make_check_stop(max_turns, guards))
    graph.add_node("finalize", make_finalize_node(caller, case_id))

    graph.add_edge(START, "hypothesis")
    graph.add_edge("hypothesis", "decide")
    graph.add_edge("decide", "absorb_panel")
    routes = {**{a: a for a in enabled}, "finalize": "finalize"}
    if panel:
        graph.add_node("challenger_final", make_challenger_final_node(caller, case_id))
        # One edge only. The bound on re-deliberation lives in `route_action`,
        # which reads `challenged_this_finalize` *before* this node sets it.
        graph.add_edge("challenger_final", "hypothesis")
        routes["challenger_final"] = "challenger_final"
    graph.add_conditional_edges(
        "absorb_panel", lambda s: route_action(s, enabled=enabled, has_challenger=panel), routes)
    for action in enabled:
        graph.add_edge(action, "check_stop")
    graph.add_conditional_edges(
        "check_stop", lambda s: route_stop(s, absorb_on_cap=True),
        {"continue": "hypothesis", "stop": "finalize", "absorb": "hypothesis_final"},
    )
    graph.add_edge("hypothesis_final", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def build_case_graph(store: Any, case_id: str, *, config: str, caller: Any, guards: Any,
                     costs: Any, max_turns: int, question_overlap: float) -> Any:
    """The graph for one case, wired identically for the CLI and the web app."""
    return build_graph(
        caller=caller, case_id=case_id,
        patient=Patient(store.patient_view(case_id), caller),
        gatekeeper=Gatekeeper(
            store.gatekeeper_view(case_id), costs,
            # Without this the cascade stops at exact/contains/synonyms, and
            # anything else becomes a fabricated "not available".
            llm_disambiguate=make_llm_disambiguator(caller, case_id),
        ),
        decision_model=make_orchestrator_decision(ENABLED_ACTIONS), enabled=ENABLED_ACTIONS,
        max_turns=max_turns, guards=guards, question_overlap=question_overlap,
        panel=config == "panel", costs=costs,
    )
