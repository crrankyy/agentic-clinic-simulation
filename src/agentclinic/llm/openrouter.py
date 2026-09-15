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

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, ValidationError

from .guards import NonRetryable, RunGuards

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Signatures of "the provider returned nothing", as they surface through the
#: OpenAI client. `choices: null` in a 200 response becomes a TypeError deep in
#: the parsing code, which is indistinguishable from a bug unless matched here.
_EMPTY_SIGNATURES = (
    "'NoneType' object is not iterable",
    "object of type 'NoneType' has no len()",
)


def _looks_empty(exc: BaseException) -> bool:
    """True for 'the provider gave us nothing', including a hung stream.

    A timeout belongs here rather than with content failures: re-prompting a
    provider that is not responding is pointless, and the right move is to wait.
    """
    if isinstance(exc, (EmptyResponse, asyncio.TimeoutError, TimeoutError)):
        return True
    return any(s in str(exc) for s in _EMPTY_SIGNATURES)


class MissingCredentials(RuntimeError):
    """`OPENROUTER_API_KEY` is not set."""


class EmptyResponse(RuntimeError):
    """The provider returned no content at all.

    Distinct from invalid content, and the distinction is load-bearing. Observed
    live on a free endpoint: it answers in ~0.3s with `choices: null` when it is
    shedding load, while a real generation takes 10-40s. Re-prompting is the
    wrong response — there is nothing to correct — and doing it immediately just
    hammers an endpoint that is already refusing. The right response is to wait.
    """


class StructuredOutputFailed(RuntimeError):
    """The model could not produce schema-valid output within the attempt budget.

    Raised rather than returning a best guess: a silently coerced object would
    put a fabricated decision into the encounter and make the failure invisible
    in the metrics.
    """

    def __init__(self, schema: str, attempts: int, last_error: str) -> None:
        super().__init__(
            f"{schema} did not validate after {attempts} attempts; last error: {last_error}"
        )
        self.schema = schema
        self.attempts = attempts


@dataclass
class Usage:
    """What one call actually cost. `cost` is None when the provider omits it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: This model is a reasoning model and spends most of its completion budget
    #: here — 36 of 39 tokens on a one-word reply. It counts towards max_tokens.
    reasoning_tokens: int = 0
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
        # Verified against a live response: OpenRouter's `usage` block arrives
        # inside `llm_output["token_usage"]`, `cost` included — there is no
        # separate "usage" key, and for a `:free` model `cost` is present and 0.
        raw = (response.llm_output or {}).get("token_usage") or {}
        cost = raw.get("cost")
        details = raw.get("completion_tokens_details") or {}
        self.last = Usage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
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
        structured_method: str | None = None,
        call_timeout: float = 120.0,
    ) -> None:
        self.model = model
        #: None lets LangChain pick; "function_calling" forces a tool call, which
        #: is the only schema mechanism some free models offer.
        self.structured_method = structured_method
        #: Hard wall-clock ceiling on one call. httpx's timeout is PER-OPERATION:
        #: a read timeout only fires after that long with *no* data, so a provider
        #: trickling bytes keeps resetting it and the call never returns. Observed
        #: live — a run sat on two ESTABLISHED sockets for 65 minutes with the
        #: event loop pumping an async generator. Only a total deadline bounds it.
        self.call_timeout = call_timeout
        self.guards = guards
        self.recorder = recorder
        self.tracer = tracer
        #: Per case, because one caller is shared by concurrent encounters and a
        #: single cumulative counter would attribute repairs to the wrong case.
        self.parse_failures_by_case: dict[str, int] = {}

    def _config(self) -> dict[str, Any]:
        """Callbacks must be passed per invocation.

        `model.with_config(callbacks=[...]).with_structured_output(S)` looks
        equivalent but is not: `with_structured_output` is proxied to the
        underlying model, so the bound config is discarded and no usage is ever
        recorded. That failure is silent — tokens and cost simply read zero.
        """
        return {"callbacks": [self.recorder]} if self.recorder is not None else {}

    def _record_failures(self, case_id: str, n: int) -> None:
        if n:
            self.parse_failures_by_case[case_id] = self.parse_failures_for(case_id) + n

    def parse_failures_for(self, case_id: str) -> int:
        return self.parse_failures_by_case.get(case_id, 0)

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
        self,
        schema: type[BaseModel],
        messages: Any,
        *,
        case_id: str,
        node: str,
        attempts: int = 3,
        transient_retries: int = 4,
    ) -> BaseModel:
        """Ask for schema-valid output, repairing on failure.

        Q-15: three attempts (initial + 2 repairs), each re-prompting with the
        validation error. The attempt counter is a **local variable** — there is
        deliberately no graph cycle here, because a cycle through the router
        would bypass `check_stop` and so escape both the turn cap and the spend
        cap.
        """
        kwargs = {"method": self.structured_method} if self.structured_method else {}
        runnable = self.model.with_structured_output(schema, **kwargs)
        config = self._config()
        prompt = messages
        last_error = ""
        content_attempts = 0
        transient = 0

        while content_attempts < attempts:
            await self._before(case_id)
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    runnable.ainvoke(prompt, config=config), timeout=self.call_timeout
                )
                self._after(case_id, node, started)
                self._record_failures(case_id, content_attempts)
                return result  # type: ignore[return-value]
            except NonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 — external API boundary
                self._after(case_id, node, started)
                elapsed = time.monotonic() - started
                last_error = f"{type(exc).__name__}: {exc}"[:400]
                empty = _looks_empty(exc)

                if empty and transient < transient_retries:
                    # Wait, do not re-prompt. The endpoint is refusing, not
                    # misunderstanding. Backoff is what actually recovers it.
                    transient += 1
                    delay = min(2 ** transient, 16)
                    if self.tracer is not None:
                        self.tracer.node(case_id=case_id, node=node, event="empty_response",
                                         attempt=transient, backoff_s=delay,
                                         elapsed_s=round(elapsed, 2))
                    await asyncio.sleep(delay)
                    continue

                content_attempts += 1
                if self.tracer is not None:
                    self.tracer.node(case_id=case_id, node=node, event="parse_failure",
                                     attempt=content_attempts, error=last_error[:200])
                prompt = (
                    f"{messages}\n\n---\nYour previous reply did not match the required "
                    f"schema. Error:\n{last_error}\nReply again, valid this time."
                )

        self._record_failures(case_id, attempts)
        raise StructuredOutputFailed(schema.__name__, attempts, last_error)

    async def text(self, messages: Any, *, case_id: str, node: str) -> str:
        await self._before(case_id)
        started = time.monotonic()
        result = await asyncio.wait_for(
            self.model.ainvoke(messages, config=self._config()), timeout=self.call_timeout
        )
        self._after(case_id, node, started)
        content = getattr(result, "content", result)
        return content if isinstance(content, str) else str(content)
