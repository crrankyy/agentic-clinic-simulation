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
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, ValidationError

from ..config import RetryPolicy
from .guards import NonRetryable, RunGuards

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Signatures of "the provider returned nothing", as they surface through the
#: OpenAI client. `choices: null` in a 200 response becomes a TypeError deep in
#: the parsing code, which is indistinguishable from a bug unless matched here.
_EMPTY_SIGNATURES = (
    "'NoneType' object is not iterable",
    "object of type 'NoneType' has no len()",
)


#: Where each failure class goes. Only `content` earns a repair prompt: it is the
#: one class where the model produced something and can be told what was wrong
#: with it. Before D-059 everything that was not string-matched as "empty" went
#: through the content path -- so a 401 or a 429 was re-sent within about a
#: second with the transport error pasted into the doctor's prompt, three
#: times, and then reported as "did not validate". That contradicted Q-08.
#: Prefix of every rejection raised by the repeat guard (D-056). The caller
#: spends a content attempt on it like any validation failure -- the model is
#: re-asked, no turn is consumed -- but logs it as `repeat_guard` and does not
#: count it as a parse failure, because the output was well-formed.
GUARD_MARKER = "REPEAT_GUARD"

FATAL_AUTH = "auth"          # 401/402/403: the key, or its spend limit
FATAL_CONFIG = "config"      # 400/404/422: model, provider or parameter not served
TRANSIENT = frozenset({"rate_limited", "server_error", "timeout", "connection", "empty"})
CONTENT = "content"


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    # LangChain surfaces an error body inside a 200 (`choices: null` plus
    # `error`) as a ValueError whose message is the dict: "{'message': ..., 'code': 502}".
    m = re.search(r"'code':\s*(\d{3})", str(exc))
    return int(m.group(1)) if m else None


def classify(exc: BaseException) -> str:
    """Name the failure by what happened, not by how its message reads."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    name = type(exc).__name__
    if name == "APITimeoutError":
        return "timeout"
    if name == "APIConnectionError":
        return "connection"
    if isinstance(exc, EmptyResponse):
        return "empty"
    if isinstance(exc, (ValidationError, NoToolCall, TruncatedOutput)) or name == "OutputParserException":
        return CONTENT
    status = _status_of(exc)
    if status is not None:
        if status in (401, 402, 403):
            return FATAL_AUTH
        if status in (400, 404, 405, 413, 422):
            return FATAL_CONFIG
        if status == 429:
            return "rate_limited"
        if status == 408:
            return "timeout"
        if status >= 500:
            return "server_error"
    msg = str(exc)
    # A 200 with `choices: null`, as it surfaces under each structured-output
    # method: forced tool calls raise the first; json_schema/json_mode the rest.
    if ("null value for `choices`" in msg or "does not have a 'parsed' field" in msg
            or any(sig in msg for sig in _EMPTY_SIGNATURES)):
        return "empty"
    return CONTENT


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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


class NoToolCall(RuntimeError):
    """A 200 whose reply was text rather than the forced tool call.

    LangChain's function-calling parser reports this by returning None, not by
    raising, so before D-059 `structured()` handed None to the node, which then
    crashed on attribute access -- or, in finalize, recorded a made-up answer as
    a voluntary one.
    """


class TruncatedOutput(RuntimeError):
    """The reply hit max_tokens and was cut off before the object was complete."""


class ProviderError(NonRetryable):
    """A provider failure that no repair prompt can fix."""

    def __init__(self, error_class: str, status: int | None, detail: str) -> None:
        super().__init__(detail)
        self.error_class = error_class
        self.status = status


class ProviderAuthError(ProviderError):
    """401/402/403. Run-fatal: every later call would fail the same way."""


class ProviderConfigError(ProviderError):
    """400/404/422: the model, provider or a parameter is not served. Run-fatal."""


class ProviderUnavailable(ProviderError):
    """Still failing after the whole transient-retry budget.

    The graph converts this into a forced finalize with stop_reason
    `provider_error` (D-059) -- outcome `error`, excluded from accuracy, instead
    of the whole case crashing with the answer already in hand.
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
        self.last_error = last_error


@dataclass
class Usage:
    """What one call actually cost. `cost` is None when the provider omits it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: This model is a reasoning model and spends most of its completion budget
    #: here — 36 of 39 tokens on a one-word reply. It counts towards max_tokens.
    reasoning_tokens: int = 0
    cost: float | None = None
    finish_reason: str | None = None
    model: str | None = None
    generation_id: str | None = None
    #: False when no response arrived at all (a timeout, a refused connection).
    #: A call that responded but failed validation was still billed.
    responded: bool = False


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
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        info = getattr(gen, "generation_info", None) or {}
        meta = getattr(getattr(gen, "message", None), "response_metadata", None) or {}
        self.last = Usage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
            cost=float(cost) if cost is not None else None,
            finish_reason=info.get("finish_reason") or meta.get("finish_reason"),
            model=(response.llm_output or {}).get("model_name") or meta.get("model_name"),
            generation_id=meta.get("id"),
            responded=True,
        )


def build_chat_model(
    *,
    model: str,
    pin_provider: list[str] | None = None,
    allow_fallbacks: bool = False,
    timeout: float = 120.0,
    attribution_title: str = "agent-clinic",
    max_tokens: int | None = None,
    reasoning: dict[str, Any] | None = None,
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
    # D-061: bounded output and bounded thinking, per role. Unbounded, the free
    # model produced 32,768-token hypothesis calls three times and one of 38,987
    # (488 s) -- free then, billed on a paid model, and each a 120 s timeout.
    if reasoning:
        extra_body["reasoning"] = dict(reasoning)

    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        temperature=0.0,
        timeout=timeout,
        # 0: LLMCaller owns retries. SDK-internal retries would multiply the
        # wall-clock deadline by (max_retries + 1) invisibly.
        max_retries=0,
        default_headers={"X-Title": attribution_title},  # Q-06: no HTTP-Referer
        extra_body=extra_body,
        **({"max_tokens": int(max_tokens)} if max_tokens else {}),
    )


#: Which role's settings a node uses, where the node name is not the role name.
#: Every other node is its own role (M-33: before D-061 one chat model built
#: from the orchestrator entry served every role, and role settings were ignored).
NODE_ROLE = {"ask_patient": "patient"}


class LLMCaller:
    """The one path a node uses to reach a model.

    Enforces the guards, records cost against the authoritative tracker, and
    emits a trace event — none of which a node should have to remember.
    """

    def __init__(
        self,
        model: BaseChatModel,
        guards: RunGuards | None = None,
        tracer: Any = None,
        structured_method: str | None = None,
        call_timeout: float = 120.0,
        *,
        role_models: dict[str, BaseChatModel] | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.model = model
        self.role_models = dict(role_models or {})
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
        self.tracer = tracer
        self.retry = retry or RetryPolicy()
        self._sleep = sleep or asyncio.sleep
        #: Per case, because one caller is shared by concurrent encounters and a
        #: single cumulative counter would attribute repairs to the wrong case.
        self.parse_failures_by_case: dict[str, int] = {}
        #: Per case, counts of transient provider failures, for the run report.
        self.transient_by_case: dict[str, int] = {}

    def model_for(self, node: str) -> BaseChatModel:
        return self.role_models.get(NODE_ROLE.get(node, node), self.model)

    def _record_failures(self, case_id: str, n: int) -> None:
        if n:
            self.parse_failures_by_case[case_id] = self.parse_failures_for(case_id) + n

    def parse_failures_for(self, case_id: str) -> int:
        return self.parse_failures_by_case.get(case_id, 0)

    def transient_failures_for(self, case_id: str) -> int:
        return self.transient_by_case.get(case_id, 0)

    async def _before(self, case_id: str) -> None:
        if self.guards is not None:
            await self.guards.before_call(case_id)

    def _after(self, case_id: str, node: str, started: float, usage: Usage, *,
               status: str, attempt: int, error_class: str | None = None) -> Usage:
        # Only a call that actually responded was billed. A 429 or a timeout
        # adds nothing; a response that failed validation adds its own cost --
        # never the previous call's, which is what the shared slot used to do.
        if self.guards is not None and usage.cost:
            self.guards.spend.add(case_id, usage.cost)
        if self.tracer is not None:
            self.tracer.llm_call(
                case_id=case_id, node=node,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost=usage.cost, latency_s=time.monotonic() - started,
                status=status, attempt=attempt, error_class=error_class,
                reasoning_tokens=usage.reasoning_tokens,
                finish_reason=usage.finish_reason, model=usage.model,
                generation_id=usage.generation_id,
            )
        return usage

    def _event(self, case_id: str, node: str, event: str, **fields: Any) -> None:
        if self.tracer is not None:
            self.tracer.node(case_id=case_id, node=node, event=event, **fields)

    async def structured(
        self,
        schema: type[BaseModel],
        messages: Any,
        *,
        case_id: str,
        node: str,
    ) -> BaseModel:
        """Ask for schema-valid output, repairing only what a repair can fix.

        Q-15: `content_attempts` attempts (initial + repairs), each re-prompting
        with the validation error. The counter is a **local variable** — there
        is deliberately no graph cycle here, because a cycle through the router
        would bypass `check_stop` and so escape both the turn cap and the spend
        cap. Transient failures back off and resend unchanged; auth and config
        failures raise at once (D-059).

        Usage is captured by a fresh recorder per attempt: one shared mutable
        slot re-counted the previous call's cost on every failure (M-14), and
        was racy across concurrent cases.
        """
        content_budget = self.retry.content_attempts
        transient_budget = self.retry.transient_attempts
        kwargs = {"method": self.structured_method} if self.structured_method else {}
        runnable = self.model_for(node).with_structured_output(schema, **kwargs)
        prompt = messages
        last_error = ""
        content_attempts = 0
        transient = 0
        timeouts = 0
        attempt = 0
        guard_rejections = 0

        while content_attempts < content_budget:
            attempt += 1
            await self._before(case_id)
            started = time.monotonic()
            rec = UsageRecorder()
            try:
                result = await asyncio.wait_for(
                    runnable.ainvoke(prompt, config={"callbacks": [rec]}),
                    timeout=self.call_timeout,
                )
                if result is None or not isinstance(result, schema):
                    if rec.last.finish_reason == "length":
                        raise TruncatedOutput(
                            "your reply was cut off at the output limit before the "
                            "object was complete; reply again, more concisely")
                    if not rec.last.responded and result is None:
                        raise EmptyResponse("no response content")
                    raise NoToolCall(
                        f"the reply did not call the required tool ({schema.__name__}); "
                        "respond ONLY by calling it with valid arguments")
            except NonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 — external API boundary
                elapsed = time.monotonic() - started
                kind = classify(exc)
                status = _status_of(exc)
                self._after(case_id, node, started, rec.last, status="failed",
                            attempt=attempt, error_class=kind)
                detail = f"{type(exc).__name__}: {exc}"[:400]

                if kind == FATAL_AUTH:
                    self._event(case_id, node, "auth_error", status=status, error=detail[:200])
                    raise ProviderAuthError(kind, status, f"provider rejected the request "
                                                          f"({status}): {detail}") from exc
                if kind == FATAL_CONFIG:
                    self._event(case_id, node, "config_error", status=status, error=detail[:200])
                    raise ProviderConfigError(kind, status, f"provider could not serve the "
                                                            f"request ({status}): {detail}") from exc

                if kind in TRANSIENT:
                    transient += 1
                    self.transient_by_case[case_id] = self.transient_failures_for(case_id) + 1
                    if kind == "timeout":
                        timeouts += 1
                    exhausted = (transient > transient_budget
                                 or (kind == "timeout" and timeouts > self.retry.timeout_retries))
                    if exhausted:
                        self._event(case_id, node, "provider_unavailable", error_class=kind,
                                    status=status, attempts=transient, error=detail[:200])
                        raise ProviderUnavailable(kind, status, f"{kind} after {transient} "
                                                                f"attempts: {detail}") from exc
                    delay = _retry_after(exc) or min(
                        self.retry.backoff_base_s * 2 ** (transient - 1), self.retry.backoff_cap_s)
                    delay *= random.uniform(0.8, 1.2)
                    event = "empty_response" if kind == "empty" else kind
                    self._event(case_id, node, event, attempt=transient, status=status,
                                backoff_s=round(delay, 2), elapsed_s=round(elapsed, 2),
                                error=detail[:200])
                    if kind == "rate_limited" and self.guards is not None:
                        # The limit is per account: every worker should wait.
                        self.guards.cooldown(delay)
                    # Wait and resend unchanged. There is nothing to correct.
                    await self._sleep(delay)
                    continue

                content_attempts += 1
                last_error = detail
                if GUARD_MARKER in detail:
                    guard_rejections += 1
                event = ("repeat_guard" if GUARD_MARKER in detail
                         else "no_tool_call" if isinstance(exc, NoToolCall)
                         else "truncated" if isinstance(exc, TruncatedOutput) else "parse_failure")
                self._event(case_id, node, event, attempt=content_attempts, error=detail[:200])
                prompt = (
                    f"{messages}\n\n---\nYour previous reply did not match the required "
                    f"schema. Error:\n{last_error}\nReply again, valid this time."
                )
                continue

            self._after(case_id, node, started, rec.last, status="ok", attempt=attempt)
            self._record_failures(case_id, content_attempts - guard_rejections)
            return result  # type: ignore[return-value]

        self._record_failures(case_id, content_budget - guard_rejections)
        raise StructuredOutputFailed(schema.__name__, content_budget, last_error)
