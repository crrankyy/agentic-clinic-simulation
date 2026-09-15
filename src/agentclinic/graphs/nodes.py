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
from .routing import check_stop
from .state import EncounterState, Event

#: Applied by every action node, so a finalize attempt after new information is
#: treated as a fresh one.
_CLEARS = {"challenged_this_finalize": False, "challenger_opinion": None, "cost_objection": None}


def make_ask_patient(patient: Any, case_id: str) -> Callable:
    async def ask_patient(state: EncounterState) -> dict[str, Any]:
        question = state.get("action_argument") or ""
        turn = int(state.get("turn", 0)) + 1
        reply = await patient.answer(question, case_id=case_id)
        return {
            "encounter_log": [
                Event(turn=turn, kind="question", actor="doctor", text=question),
                Event(turn=turn, kind="answer", actor="patient", text=reply.reply,
                      meta={"unknown": str(reply.unknown)}),
            ],
            **_CLEARS,
        }

    return budget_guarded(ask_patient, channel="encounter_log")


def make_gatekeeper_node(gatekeeper: Gatekeeper, domain: str, kind: str) -> Callable:
    async def node(state: EncounterState) -> dict[str, Any]:
        request = state.get("action_argument") or ""
        turn = int(state.get("turn", 0)) + 1
        reply = await gatekeeper.respond(request, domain)  # type: ignore[arg-type]
        events = [
            Event(turn=turn, kind=kind, actor="doctor", text=request),
            Event(turn=turn, kind=kind, actor="gatekeeper", text=reply.text,
                  meta={"tier": reply.tier, "key": str(reply.key),
                        "request": request[:120],
                        "cost_usd": f"{reply.cost_usd:.2f}"}),
        ]
        if reply.unlisted:
            events.append(Event(turn=turn, kind="unlisted_test", actor="system",
                                text=f"requested {request!r} is not in this case"))
        return {"encounter_log": events, "test_cost_usd": reply.cost_usd, **_CLEARS}

    return budget_guarded(node, channel="encounter_log")


def make_search_literature(evidence: Any, case_id: str) -> Callable:
    """Placeholder until Phase 5. Never wired unless the action is enabled."""

    async def search_literature(state: EncounterState) -> dict[str, Any]:
        query = state.get("action_argument") or ""
        turn = int(state.get("turn", 0)) + 1
        text = await evidence.search(query, case_id=case_id)
        return {
            "encounter_log": [
                Event(turn=turn, kind="literature", actor="doctor", text=query),
                Event(turn=turn, kind="literature", actor="evidence", text=text),
            ],
            **_CLEARS,
        }

    return budget_guarded(search_literature, channel="encounter_log")


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
