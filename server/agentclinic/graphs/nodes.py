"""Action nodes shared by the interactive and automated encounter graphs.

Both graphs execute the *same* patient and gatekeeper nodes. That matters: the
human-driven mode is only a useful design check if what you drive by hand is
what the model drives later.

Every action node clears `challenged_this_finalize` and the stale sub-role
opinions. That is what bounds the panel's finalize re-deliberation to one — the
flag is set by `challenger_final` and cleared the moment any real action runs.
"""

from __future__ import annotations

from typing import Any, Callable

from ..agents.gatekeeper import Gatekeeper
from .guarding import budget_guarded
from .ledger import build_ledger
from .routing import check_stop
from .state import EncounterState, Event

#: Applied by every action node, so a finalize attempt after new information is
#: treated as a fresh one.
#:
#: The opinions are deliberately NOT cleared here. `order_test` is the only
#: action that can produce a cost objection, and it runs immediately after the
#: deliberation that produced it — wiping the key here meant no orchestrator
#: ever saw one. The orchestrator clears them instead, once it has rendered them.
_CLEARS = {"challenged_this_finalize": False}


def patient_history(log: list[Event]) -> list[tuple[str, str]]:
    """The patient's own earlier exchanges: questions and answers, nothing else.

    Q-10 amended by D-058. The patient used to be given only the current
    question, so it could not keep its story straight -- "I've never been tested
    for HIV" at turns 9 and 12, "I don't know if I've ever been tested" at 16 and
    17. Results and examination findings are never included: in medqa-0002 the
    MRI text names the diagnosis, and the patient must not be able to hint at it.
    """
    pairs: list[tuple[str, str]] = []
    pending: Event | None = None
    for e in log:
        if e.kind == "question" and e.actor == "doctor":
            pending = e
        elif e.kind == "answer" and e.actor == "patient" and pending is not None:
            pairs.append((pending.text, e.text))
            pending = None
    return pairs


def make_ask_patient(patient: Any, case_id: str) -> Callable:
    async def ask_patient(state: EncounterState) -> dict[str, Any]:
        question = state.get("action_argument") or ""
        turn = int(state.get("turn", 0)) + 1
        history = patient_history(state.get("encounter_log") or [])
        reply = await patient.answer(question, case_id=case_id, history=history)
        return {
            "encounter_log": [
                Event(turn=turn, kind="question", actor="doctor", text=question),
                Event(turn=turn, kind="answer", actor="patient", text=reply.reply,
                      meta={"unknown": str(reply.unknown)}),
            ],
            **_CLEARS,
        }

    return budget_guarded(ask_patient, channel="encounter_log")


def delivered_keys(log: list[Event], kind: str) -> dict[str, int]:
    """Keys already returned in this encounter, with the turn each first arrived."""
    # Reversed, so the earliest turn is the one left in the dict.
    return {e.key: e.turn for e in reversed(build_ledger(log))
            if e.kind == kind and e.key and e.outcome in ("result", "partial")}


def make_gatekeeper_node(gatekeeper: Gatekeeper, domain: str, kind: str) -> Callable:
    async def node(state: EncounterState) -> dict[str, Any]:
        request = state.get("action_argument") or ""
        turn = int(state.get("turn", 0)) + 1
        delivered = delivered_keys(state.get("encounter_log") or [], kind)
        reply = await gatekeeper.respond(request, domain, delivered)  # type: ignore[arg-type]
        events = [
            # The doctor's own request carries what came of it. The ledger is
            # built from these (D-056): outcome, and the key for the repeat
            # guard. The key never reaches a prompt.
            Event(turn=turn, kind=kind, actor="doctor", text=request,
                  meta={"unlisted": str(reply.unlisted), "outcome": reply.outcome,
                        "key": str(reply.key),
                        "ref_turn": str(reply.ref_turn) if reply.ref_turn else ""}),
            Event(turn=turn, kind=kind, actor="gatekeeper", text=reply.text,
                  meta={"tier": reply.tier, "key": str(reply.key),
                        "request": request[:120], "outcome": reply.outcome,
                        "cost_usd": f"{reply.cost_usd:.2f}"}),
        ]
        if reply.unlisted:
            events.append(Event(turn=turn, kind="unlisted_test", actor="system",
                                text=f"requested {request!r} is not in this case"))
        return {"encounter_log": events, "test_cost_usd": reply.cost_usd, **_CLEARS}

    return budget_guarded(node, channel="encounter_log")


def make_check_stop(max_turns: int, guards: Any = None) -> Callable:
    """`check_stop` is a node so it can write `stop_reason`; routers cannot."""

    def node(state: EncounterState) -> dict[str, Any]:
        spend = guards.spend.case_total(state.get("case_id", "")) if guards else 0.0
        remaining = guards.daily.remaining() if guards else None
        return check_stop(state, max_turns=max_turns, spend_total=spend,
                          requests_remaining=remaining)

    return node


def absorb_panel(state: EncounterState) -> dict[str, Any]:
    """Fold the deliberation's own events into the encounter log, then clear.

    `panel_events` is a *last-write* channel that the subgraph fills and this
    node empties. Letting `encounter_log` itself cross the subgraph boundary
    would make the parent apply `log + (log + new)` through the `add` reducer and
    duplicate the whole history on every turn.
    """
    events = state.get("panel_events") or []
    return {"encounter_log": list(events), "panel_events": []}
