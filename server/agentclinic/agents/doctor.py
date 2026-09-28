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
from pydantic import create_model, model_validator

from ..graphs.guarding import budget_guarded
from ..graphs.ledger import (
    QUESTION_OVERLAP,
    build_ledger,
    check_repeat,
    leader_progress,
    no_yield_streak,
    render_ledger,
    unavailable_lines,
)
from ..graphs.state import EVIDENCE_KINDS, DifferentialItem, EncounterSummary, Event
from ..llm.guards import BudgetExceeded
from ..llm.openrouter import GUARD_MARKER, ProviderUnavailable, StructuredOutputFailed

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


def render_progress(summary: EncounterSummary, turn: int) -> str | None:
    """The information-only progress signal (D-060). Never a stop rule."""
    bits = []
    if summary.leader and summary.leader_since_turn is not None:
        held = max(0, turn - summary.leader_since_turn)
        bits.append(f"your leading diagnosis has not changed since turn "
                    f"{summary.leader_since_turn} ({held} turn{'s' if held != 1 else ''})")
    if summary.no_yield_streak:
        n = summary.no_yield_streak
        bits.append(f"your last {n} action{'s' if n != 1 else ''} produced no new information")
    return ("Progress: " + "; ".join(bits) + ".") if bits else None


def render_summary(summary: EncounterSummary, turn: int | None = None) -> str:
    parts = []
    if summary.findings:
        parts.append("Findings:\n" + "\n".join(f"  - {v}" for v in summary.findings))
    # D-056: what the doctor has already done, built from the log by code. The
    # doctor's own request text plus an outcome flag -- never a result.
    parts.extend(render_ledger(summary.ledger))
    for label, values in (("Ruled out", summary.ruled_out),
                          ("Open questions", summary.open_questions)):
        if values:
            parts.append(f"{label}:\n" + "\n".join(f"  - {v}" for v in values))
    if turn is not None:
        progress = render_progress(summary, turn)
        if progress:
            parts.append(progress)
    return "\n".join(parts) if parts else "(nothing recorded yet)"


def render_transcript(events: list[Event]) -> str:
    """The hypothesis node's view: evidence only (D-058).

    Its own earlier "leading: X" lines, repeated red flags, harness bookkeeping
    and -- on the panel -- the challenger's argument (attributed to "doctor")
    used to be about a third of this input. Re-reading its own past guesses as
    if they were evidence anchors the model on them; the challenger reaches the
    orchestrator as a typed opinion instead, which is where D-051 wants it.
    """
    return "\n".join(f"[turn {e.turn}] {e.actor}: {e.text}"
                     for e in events if e.kind in EVIDENCE_KINDS)


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
        previous: EncounterSummary = state.get("summary") or EncounterSummary()
        ledger = build_ledger(log)
        unavailable = unavailable_lines(ledger)
        prompt = template.format(
            objective=state.get("objective", ""),
            previous=render_differential(state.get("differential", [])),
            unavailable="\n".join(unavailable) if unavailable else "(none)",
            transcript=render_transcript(log) or "(nothing yet)",
        )
        update: HypothesisUpdate = await caller.structured(
            HypothesisUpdate, prompt, case_id=case_id, node="hypothesis"
        )

        # `findings` is the model's own summary (see HypothesisUpdate): copying
        # event text here would leak the transcript into the orchestrator's
        # prompt through the summary and defeat Q-29 entirely.
        findings = list(update.findings)

        events: list[Event] = []
        turn = int(state.get("turn", 0))
        fields: dict[str, Any] = {}
        for name, values in (("findings", findings),
                             ("ruled_out", update.ruled_out),
                             ("open_questions", update.open_questions)):
            kept, dropped = truncate_field(values)
            fields[name] = kept
            if dropped:
                events.append(Event(turn=turn, kind="hypothesis", actor="system",
                                    text=f"summary.{name}: dropped {dropped} oldest entries"))
        # The ledger is never truncated (M-43): its oldest entries are exactly
        # the refusals the repeat guard relies on.
        fields["ledger"] = ledger
        leader, since = leader_progress(previous, update.differential, turn)
        fields["leader"], fields["leader_since_turn"] = leader, since
        fields["no_yield_streak"] = no_yield_streak(ledger)

        events.append(Event(turn=turn, kind="hypothesis", actor="doctor",
                            text=f"leading: {leader or '(none)'}"))
        # A red flag is logged once, when it is first raised -- not re-emitted
        # every turn, which doubled them in the transcript.
        seen = {r.concern.strip().casefold() for r in state.get("red_flags", [])}
        events.extend(Event(turn=turn, kind="red_flag", actor="doctor", text=rf.concern)
                      for rf in update.red_flags if rf.concern.strip().casefold() not in seen)

        tracer = getattr(caller, "tracer", None)
        if tracer is not None:
            # Trace-only (M-18): what the doctor concluded this turn. Never an
            # encounter_log event -- the hypothesis reads that log and the
            # viewer streams it.
            tracer.node(case_id=case_id, node="hypothesis", event="assessment", turn=turn,
                        differential=[d.model_dump() for d in update.differential],
                        findings=findings, open_questions=list(update.open_questions),
                        ruled_out=list(update.ruled_out),
                        leader_since_turn=since, no_yield_streak=fields["no_yield_streak"])

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


#: One line per action, rendered from the run's enabled set. orchestrator.md
#: used to list `search_literature` in runs where it was disabled -- the model
#: would be right to pick it, and the schema would reject it (M-45).
ACTION_TEXT = {
    "ask_patient": "`ask_patient` — put a question to the patient. `argument` is the question.",
    "request_exam": ("`request_exam` — request a physical examination. `argument` names "
                     "the region or examination."),
    "order_test": "`order_test` — order an investigation. `argument` names the test.",
    "search_literature": "`search_literature` — consult the literature. `argument` is the query.",
}
_ACTION_ORDER = ("ask_patient", "request_exam", "order_test", "search_literature")


def render_actions(enabled: frozenset[str] | None) -> str:
    names = [a for a in _ACTION_ORDER if enabled is None or a in enabled]
    lines = [f"- {ACTION_TEXT[a]}" for a in names]
    lines.append("- `finalize` — commit to a diagnosis. Choose this when further "
                 "information is unlikely to change your answer, or when you have "
                 "what you need.")
    return "\n".join(lines)


def make_orchestrator_node(
    caller: Any, case_id: str, decision_model: type, max_turns: int,
    config_dir: Path | None = None, *,
    enabled: frozenset[str] | None = None,
    resolve: Callable[[str, str], str | None] | None = None,
    question_overlap: float = QUESTION_OVERLAP,
) -> Callable:
    """Build the node that chooses one action per turn.

    `resolve` maps a test or exam request to a case key without a model call
    (`Gatekeeper.resolve_deterministic`). It returns a key name only, so this
    node can recognise a second order for a delivered record without ever being
    able to render its contents.
    """
    template = _prompt("orchestrator.md", config_dir)
    actions = render_actions(enabled)

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

        turn = int(state.get("turn", 0))
        summary: EncounterSummary = state.get("summary", EncounterSummary())
        rendered_summary = render_summary(summary, turn + 1)
        prompt = template.format(
            actions=actions,
            objective=state.get("objective", ""),
            turn=turn + 1,
            max_turns=max_turns,
            differential=render_differential(state.get("differential", [])),
            summary=rendered_summary,
            opinions="\n\n".join(opinions),
        )

        # D-056: the repeat guard, as a validator on a per-call subclass of the
        # run's decision model. A repeat fails validation, the caller's repair
        # loop re-asks with the reason, and no turn is consumed.
        rejected: list[dict[str, str]] = []
        ledger = list(summary.ledger)

        def repeat_guard(self: Any) -> Any:
            reason = check_repeat(self.action, self.argument, ledger,
                                  resolve=resolve, question_overlap=question_overlap)
            if reason:
                rejected.append({"action": self.action, "argument": self.argument[:200],
                                 "reason": reason})
                raise ValueError(reason)
            return self

        guarded = create_model(
            getattr(decision_model, "__name__", "OrchestratorDecision"),
            __base__=decision_model,
            __validators__={"repeat_guard": model_validator(mode="after")(repeat_guard)},
        )

        def guard_events() -> list[Event]:
            return [Event(turn=turn + 1, kind="guard", actor="system",
                          text=f"blocked {r['action']}: {r['argument'][:140]}",
                          meta={"action": r["action"], "reason": r["reason"][:300]})
                    for r in rejected]

        tracer = getattr(caller, "tracer", None)
        try:
            decision = await caller.structured(
                guarded, prompt, case_id=case_id, node="orchestrator"
            )
        except StructuredOutputFailed as exc:
            if GUARD_MARKER in getattr(exc, "last_error", ""):
                # Every proposal in this decision repeated something already
                # done. That is a clinical stop, not a harness failure: finalize
                # runs normally and the case is scored (D-056).
                if tracer is not None:
                    tracer.node(case_id=case_id, node="orchestrator", event="decision",
                                turn=turn + 1, action="finalize", rejected=rejected,
                                reason="every proposal repeated an earlier action",
                                summary=rendered_summary[:6000])
                return {
                    "action": "finalize",
                    "action_argument": "",
                    "stop_reason": "no_new_actions",
                    "panel_events": guard_events(),
                    "challenger_opinion": None,
                    "cost_objection": None,
                }
            # Q-15: after the repair budget, force a finalize with a DISTINCT
            # stop reason. It must never be confused with a clinical abstention,
            # or the Phase 6 abstention experiment is contaminated.
            return {
                "action": "finalize",
                "action_argument": "",
                "stop_reason": "parse_failure",
                "parse_failures": exc.attempts,
                "panel_events": guard_events() + [
                    Event(turn=turn, kind="parse_failure", actor="system", text=str(exc)[:300])],
            }
        if tracer is not None:
            # Trace-only (M-18): why the doctor chose this, what it expected, and
            # the summary it chose from. Never an encounter_log event.
            tracer.node(case_id=case_id, node="orchestrator", event="decision",
                        turn=turn + 1, action=decision.action,
                        argument=decision.argument,
                        reason=getattr(decision, "reason", ""),
                        expected_information=getattr(decision, "expected_information", ""),
                        rejected=rejected, summary=rendered_summary[:6000])
        # One-shot: an opinion is rendered into exactly one decision, then
        # cleared here rather than by the action node. Clearing it downstream
        # meant `order_test` -- the only action that can produce an objection --
        # wiped it before any orchestrator ever saw it.
        update: dict[str, Any] = {
            "action": decision.action,
            "action_argument": decision.argument,
            "challenger_opinion": None,
            "cost_objection": None,
        }
        if rejected:
            update["panel_events"] = guard_events()
        return update

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
        if state.get("stop_reason") in ("parse_failure", "provider_error"):
            forced_reason = state["stop_reason"]
        elif state.get("budget_exhausted"):
            forced_reason = "budget_exhausted"

        if forced_reason is None:
            prompt = template.format(
                objective=state.get("objective", ""),
                differential=render_differential(differential),
                summary=render_summary(state.get("summary", EncounterSummary()), turn),
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
            except ProviderUnavailable:
                forced_reason = "provider_error"

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

    return advisory(budget_guarded(challenger, channel=channel, on_content_failure=False),
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

    return advisory(budget_guarded(cost_steward, channel="panel_events",
                                   on_content_failure=False),
                    keys=("cost_objection",))
