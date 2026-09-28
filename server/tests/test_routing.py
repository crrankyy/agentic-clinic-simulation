"""Routing priority, the turn counter, and stop reasons."""

from __future__ import annotations

import pytest

from agentclinic.graphs.routing import RoutingError, check_stop, route_action, route_stop
from agentclinic.graphs.state import new_state

ENABLED = frozenset({"ask_patient", "request_exam", "order_test"})


def state(**overrides):
    s = new_state("c", "objective")
    s.update(overrides)
    return s


@pytest.mark.parametrize("action", sorted(ENABLED))
def test_every_enabled_action_routes_to_its_node(action):
    assert route_action(state(action=action), enabled=ENABLED, has_challenger=False) == action


def test_unknown_action_raises_rather_than_falling_through():
    with pytest.raises(RoutingError, match="not routable"):
        route_action(state(action="teleport"), enabled=ENABLED, has_challenger=False)


def test_none_action_raises():
    with pytest.raises(RoutingError):
        route_action(state(action=None), enabled=ENABLED, has_challenger=False)


def test_disabled_but_valid_action_raises():
    """A real action this run did not enable is not routed."""
    with pytest.raises(RoutingError, match="order_test"):
        route_action(state(action="order_test"), enabled=frozenset({"ask_patient"}),
                     has_challenger=False)


def test_budget_exhausted_short_circuits_everything():
    """Priority 1: an edge, not a decorator side effect."""
    s = state(action="ask_patient", budget_exhausted=True)
    assert route_action(s, enabled=ENABLED, has_challenger=True) == "finalize"


def test_finalize_path_is_bounded_to_one_re_decision():
    """The flag is read *before* challenger_final writes it."""
    first = state(action="finalize", challenged_this_finalize=False)
    assert route_action(first, enabled=ENABLED, has_challenger=True) == "challenger_final"
    second = state(action="finalize", challenged_this_finalize=True)
    assert route_action(second, enabled=ENABLED, has_challenger=True) == "finalize"


def test_single_doctor_never_routes_to_a_challenger():
    s = state(action="finalize", challenged_this_finalize=False)
    assert route_action(s, enabled=ENABLED, has_challenger=False) == "finalize"


# --- check_stop -------------------------------------------------------------

def test_turn_is_a_delta_not_an_absolute():
    """The reducer adds; returning an absolute would double-count."""
    assert check_stop(state(turn=5), max_turns=20)["turn"] == 1


def test_cap_is_evaluated_post_increment():
    """turn == max_turns means the max-th action has just run."""
    assert check_stop(state(turn=18), max_turns=20).get("stop_reason") is None
    assert check_stop(state(turn=19), max_turns=20)["stop_reason"] == "turn_cap"


def test_budget_exhausted_outranks_the_turn_cap():
    out = check_stop(state(turn=19, budget_exhausted=True), max_turns=20)
    assert out["stop_reason"] == "budget_exhausted"


def test_request_cap_fires_when_the_daily_allowance_is_gone():
    out = check_stop(state(turn=1), max_turns=20, requests_remaining=0)
    assert out["stop_reason"] == "request_cap"


def test_stop_reason_is_logged_as_an_event():
    out = check_stop(state(turn=19), max_turns=20)
    assert [e.kind for e in out["encounter_log"]] == ["stop"]


def test_route_stop_reads_the_reason_check_stop_wrote():
    assert route_stop(state(stop_reason=None)) == "continue"
    assert route_stop(state(stop_reason="turn_cap")) == "stop"
