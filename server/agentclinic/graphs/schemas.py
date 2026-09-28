"""Model-facing output schemas.

The `Action` type is built **per run**, not declared as a static `Literal`.
`OrchestratorDecision.action` becomes part of the JSON schema sent to the model,
so a static literal would offer an action in configurations where it is
disabled. The model would then be
*correct* to emit it, the output would validate, and the router would have no
edge for it — a routing failure reachable by the model behaving properly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from .state import DifferentialItem, RedFlag

#: Every action the design knows about. A run enables a subset.
ALL_ACTIONS = ("ask_patient", "request_exam", "order_test")

MAX_DIFFERENTIAL = 8  # Q-14


def make_action_type(enabled: frozenset[str]) -> object:
    """Build the `Literal` of actions available to this run, plus `finalize`."""
    unknown = enabled - set(ALL_ACTIONS)
    if unknown:
        raise ValueError(f"unknown actions enabled: {sorted(unknown)}")
    return Literal[tuple(sorted(enabled) + ["finalize"])]  # type: ignore[misc]


def make_orchestrator_decision(enabled: frozenset[str]) -> type[BaseModel]:
    """One decision per turn, constrained to the run's action set."""
    return create_model(
        "OrchestratorDecision",
        action=(make_action_type(enabled), ...),
        argument=(str, Field(description="the question, exam region or test name")),
        reason=(str, Field(description="why this action now")),
        # Brief §5.3's Test-selection sub-role lives here (D-026): "what result
        # would change the differential".
        expected_information=(str, Field(description="what result would change the differential")),
    )


class HypothesisUpdate(BaseModel):
    """The differential and the summary, refreshed after each new information.

    `findings` is the model's **own short summary** of what has been learned, not
    a copy of the transcript. That distinction is the whole point of Q-29: if the
    summary were verbatim event text, the orchestrator would be reading the
    transcript through it, and for the 29 cases where the dataset embeds the
    diagnosis in test results the doctor would see it on every subsequent turn.
    """

    findings: list[str] = Field(
        default_factory=list,
        description="short clinical summary of what has been learned, in your own words",
    )
    differential: list[DifferentialItem] = Field(max_length=MAX_DIFFERENTIAL)
    ruled_out: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    # Model-reported only (D-027). The rule-based arm was dropped: Vital_Signs is
    # free text in ~16 formats including paediatric "normal for age" values, so a
    # threshold check needs an age-aware parser the brief never asked for.
    red_flags: list[RedFlag] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    """What the judge scores."""

    diagnosis: str = Field(description="empty string when abstaining")
    differential: list[DifferentialItem] = Field(max_length=MAX_DIFFERENTIAL)
    confidence: float = Field(ge=0, le=1)
    abstain: bool
    # A list rather than a boolean (Q-17): a bare flag makes `red_flag_turn`
    # nearly uninterpretable.
    red_flag: list[RedFlag] = Field(default_factory=list)
    rationale: str


class JudgeVerdict(BaseModel):
    """Deliberately has no `correct` field.

    Correctness is a *function* of `match_type` (Q-22), so a separate boolean
    would let the model emit `correct=true, match_type="broader"` and leave two
    defensible readings of the headline number. `entry_matches` covers the
    differential so top-k uses the same equivalence judgement as the verdict,
    rather than a string comparison that would disagree with it.
    """

    match_type: Literal["exact", "synonym", "broader", "narrower", "wrong"]
    entry_matches: list[bool] = Field(default_factory=list)
    reasoning: str

    @property
    def correct(self) -> bool:
        return self.match_type in {"exact", "synonym"}

    @property
    def lenient_correct(self) -> bool:
        return self.match_type != "wrong"


class ChallengerOpinion(BaseModel):
    """Advisory only (D-025) — deliberately no `should_reopen`.

    `most_dangerous_unexcluded` and `dangerous_alternative_likelihood` are two
    fields rather than one sentence because the panel's `medqa-0012` failure was
    exactly their conflation: the challenger named the most *dangerous*
    alternative, and the orchestrator promoted it to most *likely*, demoting the
    correct answer from rank 1 to rank 2 with the rationale "ranked highest
    because it is the most dangerous diagnosis if missed" (D-051). Prose cannot
    be relied on to keep severity and probability apart, so the schema does it:
    naming a danger now forces a separate, explicit likelihood judgement.
    """

    argument_against_leader: str
    most_dangerous_unexcluded: str
    dangerous_alternative_likelihood: Literal["more_likely", "comparable", "less_likely"] = Field(
        description="How likely the dangerous alternative is COMPARED TO the "
                    "current leading diagnosis. This is a judgement about "
                    "probability, not about severity: a diagnosis can be far "
                    "more dangerous and still be far less likely.",
    )


class CostStewardOpinion(BaseModel):
    """Advisory only (D-025) — deliberately no `replacement_action`."""

    objection: str | None = None
    would_change_management: bool
