"""The doctor-side nodes: hypothesis, orchestrator, finalize.

Isolation note that is easy to miss: **only `hypothesis` reads
`encounter_log`.** The orchestrator and finalize nodes are handed a structured
summary and a differential, never raw event text. That is enforced here by the
prompt builders' signatures — they are not given the log — and asserted by a
test that the orchestrator's rendered prompt contains no event text absent from
the summary. For the 29 cases where the dataset embeds the diagnosis in test
results, this is the difference between the doctor seeing it once and seeing it
on every subsequent turn.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable

from ..config import CONFIG_DIR
from ..graphs.schemas import (
    ChallengerOpinion,
    CostStewardOpinion,
    FinalAnswer,
    HypothesisUpdate,
)
from ..graphs.state import DifferentialItem, EncounterSummary, Event
from ..graphs.guarding import budget_guarded
from ..llm.guards import BudgetExceeded
from ..llm.openrouter import StructuredOutputFailed

#: Q-29 / C-18: each summary field is capped, oldest entries dropped first, and
#: every drop is logged so the loss is visible in the trace rather than silent.
SUMMARY_FIELD_CHARS = 2000


def _prompt(name: str, config_dir: Path | None = None) -> str:
    return (config_dir or CONFIG_DIR).joinpath("prompts", name).read_text(encoding="utf-8")


def render_differential(items: list[DifferentialItem]) -> str:
    if not items:
        return "(none yet)"
    return "\n".join(
        f"{i}. {d.diagnosis} (p={d.probability:.2f}) — {d.rationale}"
        for i, d in enumerate(items, 1)
    )


def render_summary(summary: EncounterSummary) -> str:
    sections = [
        ("Findings", summary.findings),
        ("Tests ordered", summary.tests_ordered),
        ("Ruled out", summary.ruled_out),
        ("Open questions", summary.open_questions),
    ]
    parts = [f"{label}:\n" + "\n".join(f"  - {v}" for v in values)
             for label, values in sections if values]
    return "\n".join(parts) if parts else "(nothing recorded yet)"


def render_transcript(events: list[Event]) -> str:
    return "\n".join(f"[turn {e.turn}] {e.actor}: {e.text}" for e in events)


def truncate_field(values: list[str], limit: int = SUMMARY_FIELD_CHARS) -> tuple[list[str], int]:
    """Drop oldest entries until the field fits. Returns the kept list and drop count."""
    kept = list(values)
    dropped = 0
    while kept and sum(len(v) + 2 for v in kept) > limit:
        kept.pop(0)
        dropped += 1
    return kept, dropped


def make_hypothesis_node(caller: Any, case_id: str, config_dir: Path | None = None) -> Callable:
    """Build the node that maintains the differential and the summary."""
    template = _prompt("hypothesis.md", config_dir)

    async def hypothesis(state: dict[str, Any]) -> dict[str, Any]:
        log: list[Event] = state.get("encounter_log", [])
        prompt = template.format(
            objective=state.get("objective", ""),
            transcript=render_transcript(log) or "(nothing yet)",
        )
        update: HypothesisUpdate = await caller.structured(
            HypothesisUpdate, prompt, case_id=case_id, node="hypothesis"
        )

        # `findings` is the model's own summary (see HypothesisUpdate): copying
        # event text here would leak the transcript into the orchestrator's
        # prompt through the summary and defeat Q-29 entirely.
        findings = list(update.findings)
        # `tests_ordered` is mechanical and safe: these are the doctor's OWN
        # requests, which it already knows it made. No result text is included.
        # The "no result" marker is safe for the same reason -- it states that
        # the case holds nothing under that request, never what it does hold.
        tests = [e.text + ("  [no result: not in this case]"
                           if e.meta.get("unlisted") == "True" else "")
                 for e in log if e.kind in {"test", "exam"} and e.actor == "doctor"]

        events: list[Event] = []
        turn = int(state.get("turn", 0))
        fields = {}
        for name, values in (("findings", findings), ("tests_ordered", tests),
                             ("ruled_out", update.ruled_out),
                             ("open_questions", update.open_questions)):
            kept, dropped = truncate_field(values)
            fields[name] = kept
            if dropped:
                events.append(Event(turn=turn, kind="hypothesis", actor="system",
                                    text=f"summary.{name}: dropped {dropped} oldest entries"))

        leader = update.differential[0].diagnosis if update.differential else "(none)"
        events.append(Event(turn=turn, kind="hypothesis", actor="doctor",
                            text=f"leading: {leader}"))
        events.extend(Event(turn=turn, kind="red_flag", actor="doctor", text=rf.concern)
                      for rf in update.red_flags)

        return {
            "summary": EncounterSummary(**fields),
            "differential": update.differential,
            "red_flags": update.red_flags,
            "encounter_log": events,
        }

    return budget_guarded(hypothesis, channel="encounter_log")


#: Rendered in words rather than passed through as the raw enum value, so the
#: orchestrator reads a probability claim instead of a token it might skim past.
_LIKELIHOOD = {
    "more_likely": "MORE likely than your leader",
    "comparable": "about as likely as your leader",
    "less_likely": "LESS likely than your leader",
}


def make_orchestrator_node(
    caller: Any, case_id: str, decision_model: type, max_turns: int,
    config_dir: Path | None = None,
) -> Callable:
    """Build the node that chooses one action per turn."""
    template = _prompt("orchestrator.md", config_dir)

    async def orchestrator(state: dict[str, Any]) -> dict[str, Any]:
        opinions = []
        if state.get("challenger_opinion"):
            op = state["challenger_opinion"]
            opinions.append(
                f"## A colleague challenges your leading diagnosis\n\n"
                f"{op.argument_against_leader}\n\n"
                f"Most dangerous alternative not excluded: "
                f"{op.most_dangerous_unexcluded}\n"
                f"Their view of how *likely* that alternative is, compared with "
                f"your current leader: **{_LIKELIHOOD[op.dangerous_alternative_likelihood]}**\n\n"
                f"Severity and probability are separate. Raise this alternative "
                f"in your differential only if the evidence makes it more likely "
                f"— not because it would be worse to miss. If it is dangerous but "
                f"less likely, the response is to exclude it, not to rank it first."
            )
        if state.get("cost_objection") and state["cost_objection"].objection:
            opinions.append(f"## Cost review\n\n{state['cost_objection'].objection}")

        prompt = template.format(
            objective=state.get("objective", ""),
            turn=int(state.get("turn", 0)) + 1,
            max_turns=max_turns,
            differential=render_differential(state.get("differential", [])),
            summary=render_summary(state.get("summary", EncounterSummary())),
            opinions="\n\n".join(opinions),
        )
        try:
            decision = await caller.structured(
                decision_model, prompt, case_id=case_id, node="orchestrator"
            )
        except StructuredOutputFailed as exc:
            # Q-15: after the repair budget, force a finalize with a DISTINCT
            # stop reason. It must never be confused with a clinical abstention,
            # or the Phase 6 abstention experiment is contaminated.
            turn = int(state.get("turn", 0))
            return {
                "action": "finalize",
                "action_argument": "",
                "stop_reason": "parse_failure",
                "parse_failures": exc.attempts,
                "panel_events": [Event(turn=turn, kind="parse_failure", actor="system",
                                       text=str(exc)[:300])],
            }
        # One-shot: an opinion is rendered into exactly one decision, then
        # cleared here rather than by the action node. Clearing it downstream
        # meant `order_test` -- the only action that can produce an objection --
        # wiped it before any orchestrator ever saw it.
        return {
            "action": decision.action,
            "action_argument": decision.argument,
            "challenger_opinion": None,
            "cost_objection": None,
        }

    return budget_guarded(orchestrator, channel="panel_events")


def make_finalize_node(caller: Any, case_id: str, config_dir: Path | None = None) -> Callable:
    """Build the node that commits to a final answer.

    Under a budget breach this must not call a model — the budget is precisely
    what has run out — so it constructs an answer from the existing differential
    in Python. When the breach happened before any differential existed, the
    result is an abstention, which the runner records as outcome `error` rather
    than a clinical judgement (D-028).
    """
    template = _prompt("finalize.md", config_dir)

    async def finalize(state: dict[str, Any]) -> dict[str, Any]:
        turn = int(state.get("turn", 0))
        differential: list[DifferentialItem] = state.get("differential", [])

        def assembled(reason: str) -> FinalAnswer:
            """Build an answer from what we already have, with no model call."""
            return FinalAnswer(
                diagnosis=differential[0].diagnosis if differential else "",
                differential=differential,
                confidence=differential[0].probability if differential else 0.0,
                abstain=not differential,
                red_flag=state.get("red_flags", []),
                rationale=(
                    f"Forced finalize ({reason}); answer assembled from the "
                    "differential without a further model call, because another "
                    "call is exactly what is unavailable or has just failed."
                ),
            )

        answer: FinalAnswer | None = None
        forced_reason: str | None = None
        if state.get("budget_exhausted"):
            forced_reason = "budget_exhausted"
        elif state.get("stop_reason") == "parse_failure":
            forced_reason = "parse_failure"

        if forced_reason is None:
            prompt = template.format(
                objective=state.get("objective", ""),
                differential=render_differential(differential),
                summary=render_summary(state.get("summary", EncounterSummary())),
            )
            try:
                answer = await caller.structured(
                    FinalAnswer, prompt, case_id=case_id, node="finalize"
                )
            except BudgetExceeded:
                # Caught here rather than left to the decorator: the decorator
                # would return a budget update with no `final`, and nothing runs
                # after finalize to supply one. A forced stop must still produce
                # an answer (PLAN.md §5 item 4).
                forced_reason = "budget_exhausted"
            except StructuredOutputFailed:
                forced_reason = "parse_failure"

        if answer is None:
            answer = assembled(forced_reason or "unknown")

        update: dict[str, Any] = {
            "final": answer,
            "encounter_log": [Event(turn=turn, kind="stop", actor="doctor",
                                    text=f"final: {answer.diagnosis or '(abstained)'}")],
        }
        if forced_reason == "budget_exhausted":
            update["budget_exhausted"] = True
        if not state.get("stop_reason"):
            update["stop_reason"] = forced_reason or "finalize"
        return update

    # skip_if_exhausted=False: finalize must still run after a breach — it is the
    # node that produces the final answer, and nothing follows it.
    return budget_guarded(finalize, channel="encounter_log", skip_if_exhausted=False)


def advisory(node: Callable, *, keys: tuple[str, ...]) -> Callable:
    """Let an advisory node fail without taking the case with it.

    `budget_guarded` covers budget breaches; this covers the other way a model
    call ends badly. An opinion that cannot be produced is simply absent — but
    an unhandled `StructuredOutputFailed` propagates out of `graph.ainvoke`, the
    runner records `crash`, and the case leaves the accuracy denominator. That
    asymmetry is itself a defect: the same failure in the orchestrator becomes a
    scored forced finalize, so the panel would lose cases the single doctor keeps.
    """

    @functools.wraps(node)
    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        try:
            return await node(state)
        except StructuredOutputFailed as exc:
            return {**{k: None for k in keys}, "parse_failures": exc.attempts}

    return wrapper


def make_challenger_node(
    caller: Any, case_id: str, *, channel: str, when: str = "scheduled",
    config_dir: Path | None = None,
) -> Callable:
    """Build the challenger — advisory only (D-025).

    It writes a **typed** `challenger_opinion` rather than relying on the
    orchestrator finding its words in the transcript. That matters: the decision
    subgraph deliberately cannot read `encounter_log`, so an opinion delivered as
    free-text event would either be invisible to the orchestrator or would force
    the raw log back across the boundary — which is the isolation this design
    exists to keep.

    `channel` differs by position: inside the subgraph events go to
    `panel_events`; `challenger_final` runs in the parent and writes
    `encounter_log` directly.
    """
    template = _prompt("challenger.md", config_dir)

    async def challenger(state: dict[str, Any]) -> dict[str, Any]:
        prompt = template.format(
            objective=state.get("objective", ""),
            differential=render_differential(state.get("differential", [])),
            summary=render_summary(state.get("summary", EncounterSummary())),
        )
        opinion: ChallengerOpinion = await caller.structured(
            ChallengerOpinion, prompt, case_id=case_id, node="challenger"
        )
        turn = int(state.get("turn", 0))
        return {
            "challenger_opinion": opinion,
            channel: [Event(turn=turn, kind="challenge", actor="doctor",
                            meta={"when": when},
                            text=f"{opinion.argument_against_leader} "
                                 f"Most dangerous unexcluded: {opinion.most_dangerous_unexcluded} "
                                 f"({_LIKELIHOOD[opinion.dangerous_alternative_likelihood]})")],
        }

    return advisory(budget_guarded(challenger, channel=channel),
                    keys=("challenger_opinion",))


def make_challenger_final_node(
    caller: Any, case_id: str, config_dir: Path | None = None
) -> Callable:
    """The challenger placed before a voluntary finalize.

    Sets `challenged_this_finalize`, which `route_action` reads **before** this
    node runs on the next pass — that ordering is what bounds the re-deliberation
    to exactly one without a counter to get wrong.
    """
    inner = make_challenger_node(caller, case_id, channel="encounter_log",
                                 when="pre_finalize", config_dir=config_dir)

    async def challenger_final(state: dict[str, Any]) -> dict[str, Any]:
        update = await inner(state)
        update["challenged_this_finalize"] = True
        return update

    return challenger_final


def make_cost_steward_node(
    caller: Any, case_id: str, costs: Any, config_dir: Path | None = None
) -> Callable:
    """Build the cost-steward — advisory only (D-025).

    It reasons from the proposed test name and the price table, and has no
    budget state: `test_cost_usd` is deliberately outside the subgraph schemas.
    Its objection informs later decisions; the order it objects to still goes
    ahead, because D-025 gives it no veto.
    """
    template = _prompt("cost_steward.md", config_dir)

    async def cost_steward(state: dict[str, Any]) -> dict[str, Any]:
        proposed = state.get("action_argument") or ""
        prompt = template.format(
            objective=state.get("objective", ""),
            proposed_test=proposed,
            cost=f"${costs.price(proposed):.0f} (illustrative, not a fee schedule)",
            differential=render_differential(state.get("differential", [])),
            summary=render_summary(state.get("summary", EncounterSummary())),
        )
        opinion: CostStewardOpinion = await caller.structured(
            CostStewardOpinion, prompt, case_id=case_id, node="cost_steward"
        )
        turn = int(state.get("turn", 0))
        events = []
        if opinion.objection:
            events.append(Event(turn=turn, kind="cost_objection", actor="doctor",
                                text=opinion.objection))
        return {"cost_objection": opinion, "panel_events": events}

    return advisory(budget_guarded(cost_steward, channel="panel_events"),
                    keys=("cost_objection",))
