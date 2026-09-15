"""Typed access to the YAML in `config/`, with CLI overrides layered on top.

Prompts live in `config/prompts/` as editable files and are loaded separately;
nothing in this module embeds prompt text (brief §12).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _load(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((config_dir or CONFIG_DIR).joinpath(name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    pin: list[str]
    allow_fallbacks: bool
    attribution_title: str


@dataclass(frozen=True)
class JudgeConfig:
    sdk: str
    model: str
    max_calls_per_run: int
    auth: str = "subscription"


@dataclass(frozen=True)
class ModelConfig:
    provider: ProviderConfig
    roles: dict[str, str]
    judge: JudgeConfig
    #: How schemas are enforced. Not every model supports native strict schemas;
    #: some only offer tool calling, which is a perfectly good mechanism when the
    #: model also supports tool_choice so the call can be forced.
    structured_output_method: str = "function_calling"

    def for_role(self, role: str) -> str:
        try:
            return self.roles[role]
        except KeyError:
            raise KeyError(
                f"no model configured for role {role!r}; known roles: {sorted(self.roles)}"
            ) from None


@dataclass(frozen=True)
class Budgets:
    max_turns: int
    recursion_limit: int
    spend_per_case_usd: float
    spend_per_run_usd: float
    rate_per_minute: float
    requests_per_day: int
    concurrency: int
    retry_attempts: int
    timeout_seconds: float


@dataclass(frozen=True)
class TestCosts:
    prices: dict[str, float]
    unknown_price: float

    def price(self, key: str) -> float:
        """Price for a test key, falling back to the table median (Q-13)."""
        return float(self.prices.get(key, self.unknown_price))


def load_models(config_dir: Path | None = None) -> ModelConfig:
    raw = _load("models.yaml", config_dir)
    p = raw["provider"]
    j = raw["judge"]
    return ModelConfig(
        provider=ProviderConfig(
            base_url=p["base_url"], pin=list(p["pin"]),
            allow_fallbacks=bool(p["allow_fallbacks"]),
            attribution_title=p["attribution_title"],
        ),
        roles=dict(raw["roles"]),
        judge=JudgeConfig(sdk=j["sdk"], model=j["model"],
                          max_calls_per_run=int(j["max_calls_per_run"]),
                          auth=j.get("auth", "subscription")),
        structured_output_method=(raw.get("structured_output") or {}).get(
            "method", "function_calling"),
    )


def load_budgets(config_dir: Path | None = None, *, max_turns: int | None = None) -> Budgets:
    raw = _load("budgets.yaml", config_dir)
    enc, spend, req, ret = raw["encounter"], raw["spend"], raw["requests"], raw["retries"]
    turns = max_turns if max_turns is not None else int(enc["max_turns"])
    # Q-27: derived from the turn cap in code so the two cannot drift apart.
    recursion = turns * int(enc["recursion_limit_multiplier"]) + int(enc["recursion_limit_headroom"])
    return Budgets(
        max_turns=turns,
        recursion_limit=recursion,
        spend_per_case_usd=float(spend["per_case_usd"]),
        spend_per_run_usd=float(spend["per_run_usd"]),
        rate_per_minute=float(req["rate_per_minute"]),
        requests_per_day=int(req["per_day"]),
        concurrency=int(req["concurrency"]),
        retry_attempts=int(ret["attempts"]),
        timeout_seconds=float(ret["timeout_seconds"]),
    )


def load_test_costs(config_dir: Path | None = None) -> TestCosts:
    raw = _load("test_costs.yaml", config_dir)
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

    env_path = path or (CONFIG_DIR.parent / ".env")
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
