"""Check the credential and the endpoint before spending anything (M-17).

The web viewer accepted a run with a revoked key and found out 0.9 s later,
after three attempts -- each surfaced as "HypothesisUpdate did not validate".
An eval with a revoked key would crash every case separately. Both checks here
cost no tokens:

1. `GET /api/v1/key` -- is the key accepted, and does it have spend left?
2. `GET /api/v1/models/{model}/endpoints` -- does the pinned provider serve this
   model, and advertise `tools` and `tool_choice` (forced tool calls need both)?

With `routing=True` it also sends one tiny completion through the pinned route.
The endpoint listing cannot see the *account's* guardrails: on 2026-09-27 it
listed first-party DeepSeek as serving deepseek-v4.1-flash with tool support,
and every real call was then refused with "Paid model training violation
(account settings)". Only a routed request shows that.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from ..config import ModelConfig

BASE = "https://openrouter.ai/api/v1"


@dataclass
class PreflightResult:
    ok: bool
    reason: str
    model: str = ""
    pin: list[str] = field(default_factory=list)
    free_tier: bool = False
    key_limit_remaining: float | None = None
    providers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def preflight(models: ModelConfig, *, api_key: str | None = None,
                    client: httpx.AsyncClient | None = None,
                    timeout: float = 10.0, routing: bool = False) -> PreflightResult:
    model = models.for_role("orchestrator")
    pin = list(models.provider.pin)
    base = PreflightResult(ok=False, reason="", model=model, pin=pin, free_tier=models.is_free)
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        base.reason = "OPENROUTER_API_KEY is not set"
        return base
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await client.get(f"{BASE}/key", headers={"Authorization": f"Bearer {key}"})
        if r.status_code in (401, 403):
            msg = (r.json().get("error") or {}).get("message", "") if r.content else ""
            base.reason = (f"OpenRouter rejected the API key ({r.status_code}"
                           f"{': ' + msg if msg else ''}). Replace OPENROUTER_API_KEY.")
            return base
        r.raise_for_status()
        data = r.json().get("data") or {}
        remaining = data.get("limit_remaining")
        base.key_limit_remaining = float(remaining) if remaining is not None else None
        if not models.is_free and remaining is not None and float(remaining) <= 0:
            base.reason = (f"the key's spend limit is exhausted (limit ${data.get('limit')}). "
                           f"{model} is a paid model: raise the key's limit on OpenRouter.")
            return base

        r = await client.get(f"{BASE}/models/{model}/endpoints",
                             headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 404:
            base.reason = f"OpenRouter does not serve {model}"
            return base
        r.raise_for_status()
        endpoints = (r.json().get("data") or {}).get("endpoints") or []
        base.providers = sorted({e.get("provider_name", "") for e in endpoints})
        if pin:
            usable = [e for e in endpoints if e.get("provider_name") in pin]
            if not usable:
                base.reason = (f"pinned provider {pin} does not serve {model}; "
                               f"available: {base.providers}")
                return base
            if models.structured_output_method == "function_calling":
                ok = [e for e in usable
                      if {"tools", "tool_choice"} <= set(e.get("supported_parameters") or [])]
                if not ok:
                    base.reason = (f"pinned provider {pin} serves {model} but does not "
                                   "advertise tools + tool_choice, which forced tool calls need")
                    return base
        if routing:
            body: dict[str, Any] = {
                "model": model, "max_tokens": 16,
                "messages": [{"role": "user", "content": "Reply with the word ok."}],
            }
            if pin:
                body["provider"] = {"order": pin,
                                    "allow_fallbacks": models.provider.allow_fallbacks}
            r = await client.post(f"{BASE}/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {key}"}, timeout=60)
            if r.status_code != 200:
                err = (r.json().get("error") or {}) if r.content else {}
                base.reason = (f"a routed test call to {pin or 'any provider'} failed "
                               f"({r.status_code}): {str(err.get('message', ''))[:300]}")
                return base
        base.ok = True
        base.reason = "ok"
        return base
    except httpx.HTTPError as exc:
        base.reason = f"could not reach OpenRouter to verify the key: {type(exc).__name__}"
        return base
    finally:
        if own:
            await client.aclose()


class CachedPreflight:
    """The web app checks on every page load and every start; cache briefly."""

    def __init__(self, models: ModelConfig, ttl_s: float = 60.0) -> None:
        self.models = models
        self.ttl_s = ttl_s
        self._at = 0.0
        self._result: PreflightResult | None = None

    async def get(self, *, force: bool = False) -> PreflightResult:
        """`force` (used before a run starts) also makes one routed call."""
        if force or self._result is None or time.monotonic() - self._at > self.ttl_s:
            self._result = await preflight(self.models, routing=force)
            self._at = time.monotonic()
        return self._result
