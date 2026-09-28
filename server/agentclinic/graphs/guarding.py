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
    node: Callable, *, channel: Channel, skip_if_exhausted: bool = True,
    on_content_failure: bool = True,
) -> Callable:
    """Turn a failed model call into a state update instead of an exception.

    Three failures are handled, each ending the encounter cleanly through
    `finalize`, which builds an answer without a model call:

    * `BudgetExceeded` -- a spend, request or daily cap;
    * `ProviderUnavailable` -- the provider still failing after the whole
      transient-retry budget (D-059). stop_reason `provider_error`, outcome
      `error`: a harness property, excluded from accuracy;
    * `StructuredOutputFailed` -- the repair budget spent (stop_reason
      `parse_failure`), unless `on_content_failure=False`.

    Before D-059 only the orchestrator, finalize and the advisory nodes handled
    a failed call; one in `hypothesis` or `ask_patient` escaped as an exception
    and the case was recorded as a crash with the answer already in hand
    (c79bb4e6, turn 6). Auth and config failures are deliberately NOT caught:
    they are run-fatal, and the runner aborts on them.

    `on_content_failure=False` is for advisory nodes, which degrade to "no
    opinion" (D-049) via `advisory()` rather than ending the encounter.

    `skip_if_exhausted=False` is for `finalize`, which must still run after a
    breach — it is the node that produces the final answer, and there is no
    recovery node after it.
    """
    from ..llm.openrouter import ProviderUnavailable, StructuredOutputFailed

    @functools.wraps(node)
    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        if skip_if_exhausted and state.get("budget_exhausted"):
            return {}
        turn = int(state.get("turn", 0))
        try:
            return await node(state)
        except BudgetExceeded as exc:
            return {
                "budget_exhausted": True,
                "stop_reason": "budget_exhausted" if exc.kind == "spend_cap" else exc.kind,
                channel: [Event(turn=turn, kind="budget", actor="system",
                                text=f"budget exhausted in {node.__name__}: {exc}")],
            }
        except ProviderUnavailable as exc:
            # `budget_exhausted` is the graph's "make no further model calls"
            # flag: route_action and check_stop short-circuit on it, and
            # finalize assembles its answer in Python.
            return {
                "budget_exhausted": True,
                "stop_reason": "provider_error",
                channel: [Event(turn=turn, kind="provider_error", actor="system",
                                text=f"provider unavailable in {node.__name__}: "
                                     f"{exc.error_class}",
                                meta={"error_class": exc.error_class,
                                      "status": str(exc.status)})],
            }
        except StructuredOutputFailed as exc:
            if not on_content_failure:
                raise
            return {
                "budget_exhausted": True,
                "stop_reason": "parse_failure",
                "parse_failures": exc.attempts,
                channel: [Event(turn=turn, kind="parse_failure", actor="system",
                                text=f"{node.__name__}: {exc}"[:300])],
            }

    return wrapper
