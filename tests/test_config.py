"""Config loading, including the derived values that must not drift."""

from __future__ import annotations

import pytest

from agentclinic.config import load_budgets, load_models, load_test_costs


def test_models_load_and_every_role_has_a_model():
    cfg = load_models()
    assert cfg.judge.sdk == "anthropic"          # D-021: explicit override of §0.2
    assert cfg.judge.model == "claude-opus-5"
    assert cfg.judge.max_calls_per_run == 200    # D-037
    for role in ("patient", "gatekeeper", "hypothesis", "orchestrator",
                 "challenger", "cost_steward", "evidence"):
        assert cfg.for_role(role).endswith(":free"), f"{role} must use a free model (D-020)"


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


def test_unknown_role_raises_rather_than_defaulting():
    with pytest.raises(KeyError, match="no model configured"):
        load_models().for_role("radiologist")


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
