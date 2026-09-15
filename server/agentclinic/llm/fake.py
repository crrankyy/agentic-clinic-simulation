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

        def _invoke(messages: Any) -> BaseModel:
            self.calls.append(messages if isinstance(messages, list) else [messages])
            nxt = self._next()
            if isinstance(nxt, schema):
                return nxt
            if isinstance(nxt, dict):
                return schema.model_validate(nxt)
            raise TypeError(
                f"scripted response for {schema.__name__} must be that model or a dict, "
                f"got {type(nxt).__name__}"
            )

        return RunnableLambda(_invoke)

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
