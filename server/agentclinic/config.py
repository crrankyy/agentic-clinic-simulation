"""Typed access to the YAML in `config/`, with CLI overrides layered on top.

Prompts live in `config/prompts/` as editable files and are loaded separately;
nothing in this module embeds prompt text (brief §12).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_DIR, REPO_ROOT  # noqa: F401  (re-exported)


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load(CONFIG_DIR.joinpath(name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ProviderConfig:
    pin: list[str]
    allow_fallbacks: bool
    attribution_title: str


@dataclass(frozen=True)
class RoleSettings:
    """Per-role request limits (D-061)."""

    max_tokens: int | None = None
    reasoning: dict[str, Any] | None = None


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    max_calls_per_run: int
    auth: str = "subscription"


@dataclass(frozen=True)
class ModelConfig:
    provider: ProviderConfig
    #: ONE model for every role (D-020), so no comparison is confounded by
    #: role/model. Per-role request limits are in `role_settings`.
    model: str
    judge: JudgeConfig
    #: How schemas are enforced. Not every model supports native strict schemas;
    #: some only offer tool calling, which is a perfectly good mechanism when the
    #: model also supports tool_choice so the call can be forced.
    structured_output_method: str = "function_calling"
    role_settings: dict[str, RoleSettings] = field(default_factory=dict)

    def settings_for(self, role: str) -> RoleSettings:
        return self.role_settings.get(role) or self.role_settings.get("default") or RoleSettings()

    @property
    def is_free(self) -> bool:
        """OpenRouter's free tier is a property of the model id (D-062)."""
        return self.model.endswith(":free")


@dataclass(frozen=True)
class RetryPolicy:
    """How each failure class is retried (Q-08, restored by D-059).

    Content failures get a repair prompt, up to `content_attempts`. Transient
    failures get jittered exponential backoff with the prompt unchanged, up to
    `transient_attempts`, honouring Retry-After; timeouts are capped separately
    because each one already cost a full `call_timeout`. Auth and config
    failures are never retried. A run takes its values from budgets.yaml; these
    defaults serve a caller built without one.
    """

    content_attempts: int = 3
    transient_attempts: int = 5
    timeout_retries: int = 2
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 60.0


@dataclass(frozen=True)
class Budgets:
    max_turns: int
    recursion_limit: int
    spend_per_case_usd: float
    spend_per_run_usd: float
    rate_per_minute: float
    requests_per_day: int
    paid_rate_per_minute: float
    concurrency: int
    timeout_seconds: float
    retry: RetryPolicy
    question_repeat_overlap: float

    def request_limits(self, free: bool) -> tuple[float, int | None]:
        """(rate per minute, requests per day or None) for the model's tier."""
        if free:
            return self.rate_per_minute, self.requests_per_day
        return self.paid_rate_per_minute, None


@dataclass(frozen=True)
class TestCosts:
    prices: dict[str, float]
    unknown_price: float

    def price(self, key: str) -> float:
        """Price for a test key, falling back to the table median (Q-13)."""
        return float(self.prices.get(key, self.unknown_price))


def load_models() -> ModelConfig:
    raw = _load("models.yaml")
    p = raw["provider"]
    j = raw["judge"]
    settings = {
        role: RoleSettings(max_tokens=(int(v["max_tokens"]) if v.get("max_tokens") else None),
                           reasoning=dict(v["reasoning"]) if v.get("reasoning") else None)
        for role, v in (raw.get("role_settings") or {}).items()
    }
    return ModelConfig(
        provider=ProviderConfig(
            pin=list(p["pin"]), allow_fallbacks=bool(p["allow_fallbacks"]),
            attribution_title=p["attribution_title"],
        ),
        role_settings=settings,
        model=raw["model"],
        judge=JudgeConfig(model=j["model"],
                          max_calls_per_run=int(j["max_calls_per_run"]),
                          auth=j.get("auth", "subscription")),
        structured_output_method=(raw.get("structured_output") or {}).get(
            "method", "function_calling"),
    )


def load_budgets(*, max_turns: int | None = None, graph: str = "single_doctor") -> Budgets:
    """budgets.yaml is the only source of these values; a missing key is an error."""
    raw = _load("budgets.yaml")
    enc, spend, req = raw["encounter"], raw["spend"], raw["requests"]
    retries = dict(raw["retries"])
    turns = max_turns if max_turns is not None else int(enc["max_turns"])
    # Q-27: derived from the turn cap *and the graph shape*, so neither can
    # drift away from the other.
    recursion = (turns * int(enc["recursion_limit_multiplier"][graph])
                 + int(enc["recursion_limit_headroom"]))
    return Budgets(
        max_turns=turns,
        recursion_limit=recursion,
        spend_per_case_usd=float(spend["per_case_usd"]),
        spend_per_run_usd=float(spend["per_run_usd"]),
        rate_per_minute=float(req["rate_per_minute"]),
        requests_per_day=int(req["per_day"]),
        paid_rate_per_minute=float(req["paid_rate_per_minute"]),
        concurrency=int(req["concurrency"]),
        timeout_seconds=float(retries.pop("timeout_seconds")),
        retry=RetryPolicy(**retries),
        question_repeat_overlap=float(raw["guard"]["question_repeat_overlap"]),
    )


def load_test_costs() -> TestCosts:
    raw = _load("test_costs.yaml")
    prices = {k: float(v) for k, v in raw["prices"].items()}
    default = raw.get("default", "median")
    unknown = statistics.median(prices.values()) if default == "median" else float(default)
    return TestCosts(prices=prices, unknown_price=unknown)


def load_dotenv(path: Path | None = None) -> list[str]:
    """Load `.env` into the process environment. Returns the names it set.

    Deliberately minimal and dependency-free. **Existing environment variables
    always win**, so an explicitly exported key is never silently overridden by
    a stale file. Values are never logged — only names are returned.
    """
    import os

    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return []
    loaded: list[str] = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
