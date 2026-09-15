"""`budget_guarded` — the decorator every LLM-calling node must wear.

Applied to **all** of them, not just the ones that seemed likely to breach.
A budget breach first seen inside `ask_patient`, the gatekeeper's LLM tier or
`finalize` would otherwise escape the graph as an exception, the runner would
record `crash`, and the case would vanish from the accuracy denominator. The
live trigger is `DailyCapExceeded`, which fires on *every* subsequent call once
the daily allowance is gone — so a single breach late in a run would silently
shrink the denominator for every case after it.

`channel` is required rather than defaulted. A node inside the decision subgraph
cannot write `encounter_log` — it is deliberately absent from the subgraph's
schemas so the `add` reducer cannot duplicate history across the boundary — and
a default would make that the easy mistake for the Phase 4 sub-role nodes.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Literal

from ..llm.guards import BudgetExceeded
from .state import Event

Channel = Literal["encounter_log", "panel_events"]


def budget_guarded(
    node: Callable, *, channel: Channel, skip_if_exhausted: bool = True
) -> Callable:
    """Turn a `BudgetExceeded` into a state update instead of an exception.

    `skip_if_exhausted=False` is for `finalize`, which must still run after a
    breach — it is the node that produces the final answer, and there is no
    recovery node after it. It constructs that answer in Python without a model
    call, because a model call is exactly what is unavailable.
    """

    @functools.wraps(node)
    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        if skip_if_exhausted and state.get("budget_exhausted"):
            return {}
        try:
            return await node(state)
        except BudgetExceeded as exc:
            turn = int(state.get("turn", 0))
            return {
                "budget_exhausted": True,
                "stop_reason": "budget_exhausted" if exc.kind == "spend_cap" else exc.kind,
                channel: [Event(turn=turn, kind="budget", actor="system",
                                text=f"budget exhausted in {node.__name__}: {exc}")],
            }

    return wrapper
