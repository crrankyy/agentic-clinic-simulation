"""The encounter state channel, and the events that flow through it.

Two properties here are load-bearing and were each the subject of an adversarial
review finding:

* **`encounter_log` is a delta channel.** Nodes return *only the new events*,
  never the accumulated list. Returning the whole list under an `operator.add`
  reducer duplicates history on every write.
* **Every key has a declared writer** (`STATE_SOURCES` below). The allow-list
  test fails if a key is added without one — which is how an undeclared channel
  for ground truth would be caught.

`EncounterState` deliberately has no field that is *written from*
`Correct_Diagnosis` or `Management_and_Follow_Up`. That, not "the diagnosis
never appears in state", is the provable guarantee: the diagnosis does appear in
`encounter_log` for the 29 flagged cases, because the gatekeeper is designed to
return the data that contains it.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

EventKind = Literal[
    "objective", "question", "answer", "exam", "test", "literature",
    "hypothesis", "challenge", "cost_objection", "red_flag",
    "unlisted_test", "parse_failure", "budget", "stop",
]

Actor = Literal["doctor", "patient", "gatekeeper", "evidence", "system"]

StopReason = Literal["finalize", "turn_cap", "spend_cap", "request_cap",
                     "budget_exhausted", "parse_failure"]
#: `crash` is deliberately absent: it means the graph raised, so `finalize`
#: never ran and `stop_reason` is None. It is a runner-level outcome instead.


class Event(BaseModel):
    """One entry in the encounter transcript."""

    turn: int
    kind: EventKind
    actor: Actor
    text: str
    meta: dict[str, str] = Field(default_factory=dict)


class RedFlag(BaseModel):
    concern: str
    turn: int


class EncounterSummary(BaseModel):
    """What the doctor actually sees. Raw messages are never resent.

    `findings` is **written by the model** in its own words (D-041), not copied
    from the transcript — copying would let the orchestrator read raw event text
    through the summary and defeat Q-29 entirely. `tests_ordered` is mechanical
    and safe: those are the doctor's own requests, and carry no result text.
    `ruled_out` and `open_questions` come from the hypothesis node's output.
    Each list is truncated oldest-first, and every drop is logged as an Event so
    the loss is visible in the trace rather than silent.
    """

    findings: list[str] = Field(default_factory=list)
    tests_ordered: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class DifferentialItem(BaseModel):
    diagnosis: str
    probability: float = Field(ge=0, le=1)
    rationale: str


class EncounterState(TypedDict, total=False):
    """The only channel between nodes. Carries `case_id`, never the case."""

    case_id: str
    objective: str
    turn: Annotated[int, operator.add]
    action: str | None
    action_argument: str | None
    encounter_log: Annotated[list[Event], operator.add]
    panel_events: list[Event]
    challenger_opinion: Any
    cost_objection: Any
    summary: EncounterSummary
    differential: list[DifferentialItem]
    red_flags: list[RedFlag]
    challenged_this_finalize: bool
    budget_exhausted: bool
    spend_usd: float
    test_cost_usd: Annotated[float, operator.add]
    parse_failures: Annotated[int, operator.add]
    stop_reason: str | None
    final: Any


#: Declared writer for every state key. The allow-list test asserts this covers
#: `EncounterState` exactly — no key without a source, no source without a key.
STATE_SOURCES: dict[str, tuple[str, ...]] = {
    "case_id": ("brief",),
    "objective": ("brief",),
    "turn": ("check_stop",),
    "action": ("orchestrator",),
    "action_argument": ("orchestrator",),
    "encounter_log": ("brief", "hypothesis", "ask_patient", "request_exam", "order_test",
                      "search_literature", "absorb_panel", "challenger_final",
                      "check_stop", "finalize"),
    "panel_events": ("doctor_panel", "absorb_panel"),
    "challenger_opinion": ("challenger", "challenger_final", "ask_patient", "request_exam",
                           "order_test", "search_literature"),
    "cost_objection": ("cost_steward", "ask_patient", "request_exam", "order_test",
                       "search_literature"),
    "summary": ("hypothesis",),
    "differential": ("hypothesis",),
    "red_flags": ("hypothesis",),
    "challenged_this_finalize": ("challenger_final", "ask_patient", "request_exam",
                                 "order_test", "search_literature"),
    "budget_exhausted": ("budget_guard",),
    "spend_usd": ("check_stop",),
    "test_cost_usd": ("request_exam", "order_test"),
    "parse_failures": ("hypothesis", "orchestrator", "challenger", "challenger_final",
                       "cost_steward", "ask_patient", "finalize", "gatekeeper"),
    "stop_reason": ("check_stop", "budget_guard", "orchestrator", "finalize"),
    "final": ("finalize",),
}

#: Case fields that must never be a source for any state key (D-016).
FORBIDDEN_SOURCES = ("correct_diagnosis", "management_and_follow_up")


def new_state(case_id: str, objective: str) -> EncounterState:
    """Initial state. Nothing here derives from a hidden case field."""
    return EncounterState(
        case_id=case_id,
        objective=objective,
        turn=0,
        action=None,
        action_argument=None,
        encounter_log=[Event(turn=0, kind="objective", actor="system", text=objective)],
        panel_events=[],
        challenger_opinion=None,
        cost_objection=None,
        summary=EncounterSummary(),
        differential=[],
        red_flags=[],
        challenged_this_finalize=False,
        budget_exhausted=False,
        spend_usd=0.0,
        test_cost_usd=0.0,
        parse_failures=0,
        stop_reason=None,
        final=None,
    )
