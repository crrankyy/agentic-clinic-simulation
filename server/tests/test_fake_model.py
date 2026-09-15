"""The scripted chat model — the thing that makes every graph test offline."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agentclinic.llm.fake import FakeChatModel, ScriptExhausted


class Decision(BaseModel):
    action: str
    argument: str


def test_returns_scripted_text_in_order():
    m = FakeChatModel(["first", "second"])
    assert m.invoke("x").content == "first"
    assert m.invoke("y").content == "second"


def test_structured_output_accepts_models_and_dicts():
    m = FakeChatModel([Decision(action="ask_patient", argument="where?"),
                       {"action": "finalize", "argument": ""}])
    runnable = m.with_structured_output(Decision)
    assert runnable.invoke("a").action == "ask_patient"
    assert runnable.invoke("b").action == "finalize"


def test_running_past_the_script_raises_instead_of_defaulting():
    """A silent default would turn 'the graph looped' into a passing test."""
    m = FakeChatModel(["only one"])
    m.invoke("x")
    with pytest.raises(ScriptExhausted):
        m.invoke("y")


def test_wrong_scripted_type_is_rejected():
    m = FakeChatModel([42])
    with pytest.raises(TypeError, match="must be that model or a dict"):
        m.with_structured_output(Decision).invoke("x")


def test_records_what_was_actually_sent():
    """Isolation tests assert on prompts as sent, not as templated."""
    m = FakeChatModel(["ok"])
    m.invoke("the patient reports chest pain")
    assert "chest pain" in m.rendered_prompts[0]
