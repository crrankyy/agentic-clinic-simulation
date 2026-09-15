"""Routing functions and the one node that writes stop reasons.

`check_stop` is a **node**, not a routing function, because it has to *write*
`stop_reason` and increment `turn`. LangGraph routing functions read state and
return an edge name; they cannot write. An earlier design had the cap logic in a
router and consequently had no legal writer for `stop_reason`, so every forced
stop would have been reported as a voluntary finalize.
"""

from __future__ import annotations

from typing import Any

from .state import EncounterState, Event


class RoutingError(RuntimeError):
    """A router received a value it has no edge for. Never silently ignored."""


def check_stop(
    state: EncounterState,
    *,
    max_turns: int,
    spend_total: float = 0.0,
    requests_remaining: int | None = None,
) -> dict[str, Any]:
    """Advance the turn counter and decide whether a cap has been reached.

    Runs exactly once per executed action in both graphs, so `turns` counts the
    same thing in each — a finalize re-deliberation does not inflate it.
    The cap is evaluated on the **post-increment** value, so `turn == max_turns`
    means the max-th action has just run and the encounter finalizes after it.
    """
    turn = int(state.get("turn", 0)) + 1
    update: dict[str, Any] = {"turn": 1, "spend_usd": spend_total}

    reason: str | None = None
    if state.get("budget_exhausted"):
        reason = "budget_exhausted"
    elif requests_remaining is not None and requests_remaining <= 0:
        reason = "request_cap"
    elif turn >= max_turns:
        reason = "turn_cap"

    if reason:
        update["stop_reason"] = reason
        update["encounter_log"] = [
            Event(turn=turn, kind="stop", actor="system", text=f"stop condition: {reason}")
        ]
    return update


def route_stop(state: EncounterState) -> str:
    """After an action: continue deliberating, or stop."""
    return "stop" if state.get("stop_reason") else "continue"


def route_action(state: EncounterState, *, enabled: frozenset[str], has_challenger: bool) -> str:
    """Dispatch the orchestrator's chosen action.

    Priority order matters and is fixed:

    1. a spent budget short-circuits to `finalize` — structurally, by edge, not
       by every downstream node happening to no-op;
    2. a second finalize attempt goes straight through, which is what bounds the
       challenger re-deliberation to exactly one;
    3. a first finalize attempt goes to the challenger, in the panel only;
    4. a named, enabled action goes to its node;
    5. anything else raises.

    A *valid but disabled* action (e.g. `search_literature` when the evidence
    agent is off) is not routed at all — it is invalid output, and the
    orchestrator repairs it internally, where the attempt counter is a local
    variable and no cycle exists.
    """
    if state.get("budget_exhausted"):
        return "finalize"

    action = state.get("action")
    if action == "finalize":
        if has_challenger and not state.get("challenged_this_finalize"):
            return "challenger_final"
        return "finalize"

    if action in enabled:
        return action

    raise RoutingError(
        f"orchestrator produced action {action!r}, which is not routable. "
        f"Enabled actions: {sorted(enabled) + ['finalize']}."
    )
