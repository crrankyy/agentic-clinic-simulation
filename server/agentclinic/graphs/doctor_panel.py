"""The doctor panel: a decision subgraph with two advisory sub-roles.

    challenge_due? ──yes──> challenger ──> orchestrator ──> order_test? ──yes──> cost_steward
          │                                     │                  │                    │
          └──no─────────────────────────────────┘                  └──no──> END <───────┘

Three properties here were each the subject of an adversarial review finding and
are easy to break by accident:

* **`encounter_log` is absent from both schemas.** That is what stops the
  orchestrator, challenger and cost-steward reading raw event text — the
  isolation is structural, not a prompt instruction. Opinions reach the
  orchestrator as **typed keys** instead.
* **`panel_events` carries `operator.add` *inside* the subgraph** and last-write
  in the parent. Three nodes append to it in sequence; under last-write the
  orchestrator's write would silently replace the challenger's, and the
  challenger's opinion would never reach the log at all.
* **The subgraph never returns `encounter_log`.** A compiled subgraph returns
  its final state for declared output keys, not a delta — so letting a reduced
  channel cross in both directions makes the parent compute
  `log + (log + new)` and double the history on every turn.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .state import DifferentialItem, EncounterSummary, Event, RedFlag


class PanelInput(TypedDict, total=False):
    """What a deliberation may see. `encounter_log` is absent on purpose."""

    objective: str
    summary: EncounterSummary
    differential: list[DifferentialItem]
    red_flags: list[RedFlag]
    turn: int
    challenged_this_finalize: bool
    budget_exhausted: bool
    # BOTH opinions must be readable, or the panel is one advisory sub-role
    # wearing the name of two. The cost-steward runs *after* the orchestrator in
    # a single invocation, so its objection can only influence a decision by
    # crossing into the next one.
    challenger_opinion: Any
    cost_objection: Any


class PanelOutput(TypedDict, total=False):
    """What a deliberation may return. `encounter_log` is absent on purpose."""

    action: str | None
    action_argument: str | None
    challenger_opinion: Any
    cost_objection: Any
    panel_events: list[Event]
    parse_failures: int
    budget_exhausted: bool
    stop_reason: str | None


class PanelState(PanelInput, PanelOutput, total=False):
    """Internal state. Note the reducer — see the module docstring."""

    panel_events: Annotated[list[Event], operator.add]


def challenge_due(state: PanelState) -> str:
    """Every third executed turn, but never twice for one finalize attempt.

    The `challenged_this_finalize` clause is deliberate: after
    `challenger_final` runs, the re-deliberation would otherwise fire the
    scheduled challenger again in the same turn.
    """
    turn = int(state.get("turn", 0))
    if turn > 0 and turn % 3 == 0 and not state.get("challenged_this_finalize"):
        return "challenger"
    return "orchestrator"


def cost_due(state: PanelState) -> str:
    """The cost-steward reviews test orders, and only test orders."""
    return "cost_steward" if state.get("action") == "order_test" else "done"


def build_solo_decide(orchestrator: Callable) -> Any:
    """The single doctor's deliberation: the orchestrator alone, behind the
    same boundary schemas as the panel."""
    sub = StateGraph(PanelState, input_schema=PanelInput, output_schema=PanelOutput)
    sub.add_node("orchestrator", orchestrator)
    sub.add_edge(START, "orchestrator")
    sub.add_edge("orchestrator", END)
    return sub.compile()


def build_doctor_panel(
    *, orchestrator: Callable, challenger: Callable, cost_steward: Callable
) -> Any:
    """Compile the panel deliberation subgraph."""
    sub = StateGraph(PanelState, input_schema=PanelInput, output_schema=PanelOutput)
    sub.add_node("challenger", challenger)
    sub.add_node("orchestrator", orchestrator)
    sub.add_node("cost_steward", cost_steward)

    sub.add_conditional_edges(
        START, challenge_due,
        {"challenger": "challenger", "orchestrator": "orchestrator"},
    )
    sub.add_edge("challenger", "orchestrator")
    sub.add_conditional_edges(
        "orchestrator", cost_due, {"cost_steward": "cost_steward", "done": END}
    )
    sub.add_edge("cost_steward", END)
    return sub.compile()
