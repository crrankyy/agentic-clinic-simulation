"""Build the caller and guards for a run, in one place.

The CLI and the web engine used to wire these separately, and drifted: both
built a single chat model from the orchestrator's entry (so every other role in
models.yaml was silently ignored, M-33), both applied free-tier throttles to any
model (M-34), and the web engine kept one spend tracker for the life of the
process, so the $0.50 per-case cap became a lifetime cap per case id (M-35).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Budgets, ModelConfig
from .guards import DailyRequestCounter, RunGuards, SpendTracker, TokenBucket
from .openrouter import NODE_ROLE, LLMCaller, build_chat_model


def build_caller(
    models: ModelConfig,
    budgets: Budgets,
    *,
    guards: RunGuards | None,
    tracer: Any = None,
    pin_override: list[str] | None = None,
    unpinned: bool = False,
) -> LLMCaller:
    """One chat model per distinct (model, max_tokens, reasoning); one caller."""
    pin = None if unpinned else (pin_override or models.provider.pin)
    built: dict[tuple[str, int | None, str], Any] = {}
    role_models: dict[str, Any] = {}
    for role in sorted(set(NODE_ROLE.values())):
        model_id = models.roles.get(role) or models.for_role("orchestrator")
        st = models.settings_for(role)
        sig = (model_id, st.max_tokens, json.dumps(st.reasoning, sort_keys=True))
        if sig not in built:
            built[sig] = build_chat_model(
                model=model_id,
                pin_provider=pin,
                allow_fallbacks=models.provider.allow_fallbacks,
                attribution_title=models.provider.attribution_title,
                timeout=budgets.timeout_seconds,
                # 0: LLMCaller owns retries. SDK-internal retries would multiply
                # the wall-clock deadline by (max_retries + 1) invisibly.
                max_retries=0,
                max_tokens=st.max_tokens,
                reasoning=st.reasoning,
                require_parameters=models.provider.require_parameters,
            )
        role_models[role] = built[sig]
    return LLMCaller(
        role_models["orchestrator"],
        guards=guards,
        tracer=tracer,
        structured_method=models.structured_output_method,
        call_timeout=budgets.timeout_seconds,
        role_models=role_models,
        retry=budgets.retry_policy(),
    )


def build_shared_limits(models: ModelConfig, budgets: Budgets,
                        daily_path: Path) -> tuple[TokenBucket, DailyRequestCounter]:
    """The account-wide limits, for the model's tier (D-062). Share these."""
    rate, per_day = budgets.request_limits(models.is_free)
    return (TokenBucket(rate_per_minute=rate, capacity=rate),
            DailyRequestCounter(daily_path, limit=per_day))


def build_guards(models: ModelConfig, budgets: Budgets, daily_path: Path, *,
                 shared: tuple[TokenBucket, DailyRequestCounter] | None = None) -> RunGuards:
    """Guards for ONE run: shared account limits, a fresh spend tracker."""
    bucket, daily = shared or build_shared_limits(models, budgets, daily_path)
    return RunGuards(bucket=bucket, daily=daily,
                     spend=SpendTracker(per_case_cap=budgets.spend_per_case_usd,
                                        per_run_cap=budgets.spend_per_run_usd))


def projected_requests(config: str, max_turns: int) -> int:
    """Worst-case requests for one case, measured on a scripted model."""
    return max_turns * (7 if config == "panel" else 3) + 3
