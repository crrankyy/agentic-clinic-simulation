"""OpenRouter access, and the single call path every node goes through.

Client approach is Q-03's hybrid: LangChain's `ChatOpenAI` pointed at
OpenRouter's OpenAI-compatible endpoint — so `with_structured_output` and tool
binding work without reimplementation — plus a callback that captures
OpenRouter's own `usage` block, which LangChain does not surface and which is
the only source of real cost.

Everything funnels through `LLMCaller` rather than nodes holding models
directly, because three things must happen on *every* call and none of them can
be left to a node author remembering: the shared rate/daily/spend guards, cost
accounting into the authoritative tracker (D-039), and a trace record.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult
from pydantic import BaseModel

from .guards import RunGuards

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class MissingCredentials(RuntimeError):
    """`OPENROUTER_API_KEY` is not set."""


@dataclass
class Usage:
    """What one call actually cost. `cost` is None when the provider omits it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None


class UsageRecorder(AsyncCallbackHandler):
    """Captures OpenRouter's `usage` block from the raw response.

    OpenRouter returns `usage.cost` in dollars when asked with
    `usage: {include: true}`. For `:free` models it may be absent or zero — the
    report prints "0.0 (free tier)" rather than implying a measured value.
    """

    def __init__(self) -> None:
        self.last: Usage = Usage()

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        raw = (response.llm_output or {}).get("token_usage") or {}
        meta = (response.llm_output or {}).get("usage") or {}
        cost = meta.get("cost", raw.get("cost"))
        self.last = Usage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            cost=float(cost) if cost is not None else None,
        )


def build_chat_model(
    *,
    model: str,
    temperature: float = 0.0,
    pin_provider: list[str] | None = None,
    allow_fallbacks: bool = False,
    timeout: float = 120.0,
    max_retries: int = 3,
    attribution_title: str = "agent-clinic",
) -> BaseChatModel:
    """Build a `ChatOpenAI` aimed at OpenRouter.

    `pin_provider` + `allow_fallbacks=False` (D-030) keeps a reported run on one
    upstream provider, so two runs of "the same" configuration cannot differ for
    reasons invisible in the results.
    """
    from langchain_openai import ChatOpenAI  # imported lazily: heavy, and unused offline

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingCredentials(
            "OPENROUTER_API_KEY is not set. Export it, or run with the fake model."
        )

    extra_body: dict[str, Any] = {"usage": {"include": True}}
    if pin_provider is not None:
        extra_body["provider"] = {"order": pin_provider, "allow_fallbacks": allow_fallbacks}

    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        default_headers={"X-Title": attribution_title},  # Q-06: no HTTP-Referer
        extra_body=extra_body,
    )


class LLMCaller:
    """The one path a node uses to reach a model.

    Enforces the guards, records cost against the authoritative tracker, and
    emits a trace event — none of which a node should have to remember.
    """

    def __init__(
        self,
        model: BaseChatModel,
        guards: RunGuards | None = None,
        recorder: UsageRecorder | None = None,
        tracer: Any = None,
    ) -> None:
        self.model = model
        self.guards = guards
        self.recorder = recorder
        self.tracer = tracer

    async def _before(self, case_id: str) -> None:
        if self.guards is not None:
            await self.guards.before_call(case_id)

    def _after(self, case_id: str, node: str, started: float) -> Usage:
        usage = self.recorder.last if self.recorder else Usage()
        if self.guards is not None and usage.cost:
            self.guards.spend.add(case_id, usage.cost)
        if self.tracer is not None:
            self.tracer.llm_call(
                case_id=case_id, node=node,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost=usage.cost, latency_s=time.monotonic() - started,
            )
        return usage

    async def structured(
        self, schema: type[BaseModel], messages: Any, *, case_id: str, node: str
    ) -> BaseModel:
        await self._before(case_id)
        started = time.monotonic()
        result = await self.model.with_structured_output(schema).ainvoke(messages)
        self._after(case_id, node, started)
        return result  # type: ignore[return-value]

    async def text(self, messages: Any, *, case_id: str, node: str) -> str:
        await self._before(case_id)
        started = time.monotonic()
        result = await self.model.ainvoke(messages)
        self._after(case_id, node, started)
        content = getattr(result, "content", result)
        return content if isinstance(content, str) else str(content)
