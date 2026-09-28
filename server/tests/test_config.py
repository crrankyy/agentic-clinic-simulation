"""Config loading, including the derived values that must not drift."""

from __future__ import annotations

import pytest

from agentclinic.config import load_budgets, load_models, load_test_costs


def test_models_load_with_one_model_for_every_role():
    cfg = load_models()
    assert cfg.judge.model == "claude-opus-5"    # D-021: Anthropic SDK, not OpenRouter
    assert cfg.judge.max_calls_per_run == 200    # D-037
    # D-020's surviving half (D-061 superseded ":free" at the user's direction):
    # one model for every role, so no comparison is confounded by role/model.
    # A single key makes that structural (D-064).
    assert isinstance(cfg.model, str) and cfg.model


def test_every_role_has_bounded_output():
    """D-061: unbounded, hypothesis calls ran to 32-39k tokens and 488 s."""
    cfg = load_models()
    for role in ("patient", "gatekeeper", "hypothesis", "orchestrator", "finalize",
                 "challenger", "cost_steward"):
        assert cfg.settings_for(role).max_tokens, f"{role} has no max_tokens"


def test_free_tier_limits_apply_only_to_free_models():
    """D-062: the 1000/day cap is a property of OpenRouter's :free tier."""
    b = load_budgets()
    assert b.request_limits(free=True) == (18.0, 1000)
    rate, per_day = b.request_limits(free=False)
    assert per_day is None and rate > 18


def test_provider_is_pinned_for_reproducibility():
    """Assert the property, not the vendor: the model may change (it has)."""
    cfg = load_models()
    assert cfg.provider.pin, "a reported run must pin its provider"
    assert cfg.provider.allow_fallbacks is False


def test_structured_output_method_is_declared():
    """Not every model has native strict schemas; the mechanism must be explicit."""
    assert load_models().structured_output_method in {
        "function_calling", "json_mode", "json_schema"
    }


def test_recursion_limit_is_derived_from_the_turn_cap():
    """Q-27: a bare constant could drift away from the real stop condition."""
    assert load_budgets().recursion_limit == 20 * 6 + 20
    assert load_budgets(max_turns=10).recursion_limit == 10 * 6 + 20


def test_budget_values_match_the_decisions():
    b = load_budgets()
    assert (b.max_turns, b.spend_per_case_usd, b.spend_per_run_usd) == (20, 0.50, 25.0)
    assert (b.rate_per_minute, b.requests_per_day) == (18.0, 1000)


def test_unknown_test_is_priced_at_the_table_median():
    """Q-13: not zero (which invites ordering) and not a penalty."""
    costs = load_test_costs()
    assert costs.price("Complete_Blood_Count") == 25.0
    assert costs.price("Some_Test_Nobody_Configured") == costs.unknown_price
    assert min(costs.prices.values()) < costs.unknown_price < max(costs.prices.values())


def test_recursion_limit_follows_the_graph_shape():
    """The panel needs more supersteps per turn than the single doctor.

    One shared multiplier meant the panel hit GraphRecursionError after about ten
    finalize re-deliberations -- and crashed precisely on the cases where the
    challenger kept changing the orchestrator's mind, which is the phenomenon the
    panel arm exists to measure.
    """
    solo = load_budgets(graph="single_doctor").recursion_limit
    panel = load_budgets(graph="panel").recursion_limit
    assert panel > solo, "the panel's loop is longer per turn"
    assert solo == 20 * 6 + 20
    assert panel == 20 * 10 + 20


def test_an_unknown_graph_is_rejected_rather_than_defaulted():
    with pytest.raises(KeyError):
        load_budgets(graph="not_a_graph")
