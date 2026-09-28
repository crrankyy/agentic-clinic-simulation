"""The patient agent: answers questions from `Patient_Actor`, and nothing else.

Behaviour on facts the case file does not cover is Q-10's split, and it is not
arbitrary. A blanket "I don't know" is unrealistic in bulk — a real patient
knows whether they smoke — and a doctor quickly learns to read repeated
ignorance as a signal, which games the encounter. A blanket plausible negative
invents findings, which brief §5.1 forbids. So: natural negatives for what a
patient would know about themselves, explicit ignorance for anything clinical.

The `unknown` flag on every reply makes the rate measurable per run, which is
the only way to notice a patient model that has started fabricating.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel

from ..config import CONFIG_DIR
from ..data.views import PatientView

PROMPT = "patient.md"


class PatientReply(BaseModel):
    reply: str
    unknown: bool


def _render_value(value: Any) -> str:
    """Flatten a field that may be a string, a dict or a list.

    `Review_of_Systems` is a dict in 10 cases and `Past_Medical_History` a list
    or dict in 3, so a `str()` here would produce Python repr in the prompt.
    """
    if value is None:
        return "not recorded"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(_render_value(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k.replace('_', ' ')}: {_render_value(v)}" for k, v in value.items())
    return str(value)


def render_history(history: Sequence[tuple[str, str]]) -> str:
    if not history:
        return "(nothing yet -- this is the first question)"
    return "\n\n".join(f"Doctor: {q}\nYou: {a}" for q, a in history)


def build_prompt(view: PatientView, question: str,
                 history: Sequence[tuple[str, str]] = ()) -> str:
    """Render the patient prompt from the view. No other case data is reachable.

    `history` is the patient's own earlier questions and answers (D-058) -- never
    results or findings, which the caller filters out.
    """
    template = CONFIG_DIR.joinpath("prompts", PROMPT).read_text(encoding="utf-8")
    extra = ""
    if view.extra_fields:
        extra = "\n".join(
            f"{k.replace('_', ' ')}: {_render_value(v)}" for k, v in view.extra_fields.items()
        )
    return template.format(
        demographics=view.demographics,
        history=view.history,
        primary_symptom=view.primary_symptom or "not recorded",
        secondary_symptoms="; ".join(view.secondary_symptoms) or "none recorded",
        past_medical_history=_render_value(view.past_medical_history),
        social_history=_render_value(view.social_history),
        review_of_systems=_render_value(view.review_of_systems),
        extra_fields=extra,
        conversation=render_history(history),
        question=question,
    )


class Patient:
    """Wraps a `PatientView` and an `LLMCaller`."""

    def __init__(self, view: PatientView, caller: Any) -> None:
        self.view = view
        self.caller = caller

    async def answer(self, question: str, *, case_id: str,
                     history: Sequence[tuple[str, str]] = ()) -> PatientReply:
        prompt = build_prompt(self.view, question, history)
        return await self.caller.structured(  # type: ignore[return-value]
            PatientReply, prompt, case_id=case_id, node="ask_patient"
        )
