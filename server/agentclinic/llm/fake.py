"""A scripted chat model, so every graph runs offline and deterministically.

Brief §11 requires this: "a fake chat model that returns scripted responses and
plugs into the same interface the graphs use, so all graphs run offline and
deterministically". It therefore implements `BaseChatModel` rather than being a
bespoke stub — a stub that does not go through the same interface cannot prove
the graph works.

`with_structured_output` is overridden instead of inherited because the
inherited implementation negotiates tool calling or JSON mode with a real
provider. Here the script simply supplies already-valid objects.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from .guards import NonRetryable


class ScriptExhausted(NonRetryable, RuntimeError):
    """The graph asked for more responses than the test scripted.

    Raised rather than returning a default, because a silent default turns "the
    graph looped more than expected" into a passing test.
    """


class FakeChatModel(BaseChatModel):
    """Returns queued responses in order. Records every prompt it was given."""

    responses: deque[Any]
    calls: list[list[BaseMessage]]

    def __init__(self, responses: Sequence[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(
            responses=deque(responses or []), calls=[], **kwargs
        )

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _next(self) -> Any:
        if not self.responses:
            raise ScriptExhausted(
                f"the graph requested response #{len(self.calls)} but the script "
                "provided fewer. Either the graph looped, or the script is short."
            )
        return self.responses.popleft()

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        nxt = self._next()
        text = nxt if isinstance(nxt, str) else str(nxt)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def with_structured_output(
        self, schema: type[BaseModel], **kwargs: Any
    ) -> Runnable[Any, BaseModel]:
        """Return queued objects, validating them against `schema`.

        Accepts either an instance of `schema` or a dict, so scripts can be
        written in whichever form reads better at the call site.
        """

        def _invoke(messages: Any) -> BaseModel | None:
            self.calls.append(messages if isinstance(messages, list) else [messages])
            nxt = self._next()
            # A scripted exception is raised as the provider would raise it, so
            # the caller's failure classification is exercised offline (D-059).
            if isinstance(nxt, BaseException):
                raise nxt
            # None is what LangChain's function-calling parser returns when the
            # model replied in text instead of calling the tool.
            if nxt is None:
                return None
            if isinstance(nxt, schema):
                return nxt
            if isinstance(nxt, BaseModel):
                # e.g. an instance of the run's decision model, while the node
                # validates against a per-call subclass carrying the repeat guard.
                return schema.model_validate(nxt.model_dump())
            if isinstance(nxt, dict):
                return schema.model_validate(nxt)
            raise TypeError(
                f"scripted response for {schema.__name__} must be that model or a dict, "
                f"got {type(nxt).__name__}"
            )

        async def _ainvoke(messages: Any, config: Any = None) -> BaseModel | None:
            result = _invoke(messages)
            # A real provider that answered fires on_llm_end, which is how the
            # caller tells "replied in text" (NoToolCall, repair) from "no
            # response at all" (transient, wait). A scripted exception raised
            # above never gets here -- no response, as with a real failure.
            from langchain_core.outputs import LLMResult

            finish = "tool_calls" if result is not None else "stop"
            # LangChain has turned the list into a callback manager by now.
            callbacks = (config or {}).get("callbacks")
            for cb in getattr(callbacks, "handlers", callbacks) or []:
                if hasattr(cb, "on_llm_end"):
                    await cb.on_llm_end(LLMResult(
                        generations=[[ChatGeneration(message=AIMessage(content=""),
                                                     generation_info={"finish_reason": finish})]],
                        llm_output={"token_usage": {}}))
            return result

        return RunnableLambda(_invoke, afunc=_ainvoke)

    # --- test helpers -----------------------------------------------------
    @property
    def rendered_prompts(self) -> list[str]:
        """Every prompt the graph actually sent, flattened to text.

        Used by the isolation tests: what matters is not what a prompt template
        says, but what was sent.
        """
        out: list[str] = []
        for call in self.calls:
            parts = []
            for m in call:
                content = getattr(m, "content", m)
                parts.append(content if isinstance(content, str) else str(content))
            out.append("\n".join(parts))
        return out
