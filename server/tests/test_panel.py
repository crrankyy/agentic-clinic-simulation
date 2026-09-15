"""The doctor panel: scheduling, advisory semantics, and the three defects
adversarial review round 3 predicted would recur if untested.
"""

from __future__ import annotations

import pytest

from agentclinic.graphs.doctor_panel import (
    PanelInput,
    PanelOutput,
    build_doctor_panel,
    challenge_due,
    cost_due,
)
from agentclinic.graphs.state import new_state
from graph_fixtures import CHALLENGE, COST_OBJECT, COST_OK, FINAL, HYP, build_panel, decide

CFG = {"recursion_limit": 120}


async def run(graph, store, case_id="medqa-0010"):
    view = store.doctor_view(case_id)
    return await graph.ainvoke(new_state(case_id, view.objective_for_doctor), CFG)


# --- structure --------------------------------------------------------------

def test_the_subgraph_cannot_see_the_encounter_log():
    """Structural isolation, asserted against the COMPILED graph.

    Checking `__annotations__` alone would pass if `build_doctor_panel` were
    changed to hand `PanelState` in as its `input_schema`, which is exactly the
    mutation that would reopen the hole.
    """
    from graph_fixtures import CHALLENGE  # noqa: F401  (import kept local)

    async def noop(state):
        return {}

    compiled = build_doctor_panel(orchestrator=noop, challenger=noop, cost_steward=noop)
    channels = set(compiled.builder.channels)
    assert "encounter_log" not in channels, f"encounter_log reachable: {sorted(channels)}"
    assert "encounter_log" not in PanelInput.__annotations__
    assert "encounter_log" not in PanelOutput.__annotations__


def test_both_opinions_can_cross_the_boundary():
    """BOTH, not one.

    `cost_objection` was in the output schema but not the input, so the
    cost-steward — which runs after the orchestrator — could never influence a
    decision. The panel was one advisory sub-role calling itself two.
    """
    for key in ("challenger_opinion", "cost_objection"):
        assert key in PanelInput.__annotations__, f"{key} cannot reach the orchestrator"
        assert key in PanelOutput.__annotations__, f"{key} cannot leave the subgraph"


@pytest.mark.parametrize("turn,challenged,expected", [
    (0, False, "orchestrator"),   # nothing to challenge on turn zero
    (1, False, "orchestrator"),
    (3, False, "challenger"),     # every third turn
    (3, True, "orchestrator"),    # not twice in one finalize attempt
    (6, False, "challenger"),
])
def test_challenger_schedule(turn, challenged, expected):
    assert challenge_due({"turn": turn, "challenged_this_finalize": challenged}) == expected


def test_cost_steward_reviews_test_orders_only():
    assert cost_due({"action": "order_test"}) == "cost_steward"
    for action in ("ask_patient", "request_exam", "finalize", None):
        assert cost_due({"action": action}) == "done"


# --- C-1: the re-decision must happen, exactly once, on new information -----

async def test_finalize_re_decision_happens_exactly_once_and_on_new_information(cases):
    """Round 3's headline finding.

    The earlier design produced a re-decision the orchestrator could not
    distinguish from its first: identical inputs one boolean apart. The
    challenger's opinion must actually reach it, and the loop must still be
    bounded to one extra pass.
    """
    script = [
        HYP, decide("finalize"),                 # turn 0: orchestrator says finalize
        CHALLENGE,                               # challenger_final
        HYP, decide("finalize"),                 # re-deliberation: finalize again
        FINAL,
    ]
    graph, model, store = build_panel(cases, script)
    out = await run(graph, store)

    assert out["final"] is not None
    assert out["stop_reason"] == "finalize"

    orchestrator_prompts = [p for p in model.rendered_prompts if "Choose exactly one action" in p]
    assert len(orchestrator_prompts) == 2, "exactly one re-decision, no more"
    assert orchestrator_prompts[0] != orchestrator_prompts[1], (
        "the re-decision saw identical input — the challenger's opinion never arrived"
    )
    assert "colleague challenges" in orchestrator_prompts[1], (
        "the second prompt must carry the challenger's opinion"
    )
    assert "colleague challenges" not in orchestrator_prompts[0]


async def test_an_executed_action_clears_the_finalize_flag(cases):
    """So a finalize after new information is treated as a fresh attempt."""
    script = [
        HYP, decide("finalize"), CHALLENGE,      # first finalize -> challenged
        HYP, decide("ask_patient", "q"),         # re-decision: gather instead
        HYP, decide("finalize"), CHALLENGE,      # finalize again -> challenged AGAIN
        HYP, decide("finalize"), FINAL,
    ]
    graph, model, store = build_panel(cases, script)
    out = await run(graph, store)
    assert out["final"] is not None
    challenges = [e for e in out["encounter_log"] if e.kind == "challenge"]
    assert len(challenges) == 2, "the flag must reset when a real action runs"


# --- C-6: panel_events must accumulate, not overwrite -----------------------

async def test_three_panel_turns_leave_the_log_unduplicated_and_complete(cases):
    """Under last-write the orchestrator's write would replace the challenger's.

    Every scripted response carries a unique marker, because identical text would
    make legitimately distinct events look like duplication and hide the bug this
    test exists to catch. Two events *can* share a turn — the finalize
    re-deliberation runs `hypothesis` again without `check_stop` in between — so
    the turn number alone is not a distinguisher either.
    """
    def hyp(tag):
        return {**HYP, "differential": [{"diagnosis": f"Endometritis {tag}",
                                         "probability": 0.5, "rationale": "r"}]}

    def challenge(tag):
        return {**CHALLENGE, "argument_against_leader": f"weak point {tag}"}

    # Distinct tests per turn: ordering the same test twice legitimately produces
    # identical request/result events, which would look like duplication.
    script = [
        hyp("a"), decide("order_test", "CBC"), COST_OK,
        hyp("b"), decide("order_test", "urinalysis"),
        {**COST_OBJECT, "objection": "objection b"},
        hyp("c"), decide("order_test", "bone scan"),
        {**COST_OBJECT, "objection": "objection c"},
        hyp("d"), challenge("d"), decide("finalize"),
        challenge("final"), hyp("e"), decide("finalize"), FINAL,
    ]
    graph, model, store = build_panel(cases, script)
    out = await run(graph, store)

    log = out["encounter_log"]
    texts = [e.text for e in log]
    assert len(texts) == len(set(texts)), (
        f"duplicated events: {[t for t in texts if texts.count(t) > 1]}"
    )
    # Both sub-roles must survive the subgraph boundary.
    assert sum(e.kind == "challenge" for e in log) == 2, "scheduled + pre-finalize"
    assert sum(e.kind == "cost_objection" for e in log) == 2, "cost-steward events dropped"


async def test_turn_three_runs_both_the_scheduled_and_the_final_challenger(cases):
    """Documented, not accidental.

    Q-16 asks for the challenger every third turn *and* before every finalize.
    When the orchestrator chooses finalize on a third turn, both fire — one
    extra call. Avoiding it would mean knowing the decision before making it.
    """
    script = [
        HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2"),
        HYP, decide("ask_patient", "q3"),
        HYP, CHALLENGE, decide("finalize"),      # turn 3: scheduled challenger
        CHALLENGE,                                # challenger_final
        HYP, decide("finalize"), FINAL,
    ]
    graph, _, store = build_panel(cases, script)
    out = await run(graph, store)
    at_turn_3 = [e for e in out["encounter_log"] if e.kind == "challenge" and e.turn == 3]
    assert len(at_turn_3) == 2


# --- C-7: a no-op deliberation must not re-absorb the previous one ----------

async def test_absorb_panel_clears_its_channel(cases):
    """`absorb_panel` must empty `panel_events`, or it re-appends them."""
    from agentclinic.graphs.nodes import absorb_panel
    from agentclinic.graphs.state import Event

    events = [Event(turn=1, kind="challenge", actor="doctor", text="x")]
    out = absorb_panel({"panel_events": events})
    assert out["encounter_log"] == events
    assert out["panel_events"] == [], "must clear, or the next absorb duplicates"
    assert absorb_panel({"panel_events": []})["encounter_log"] == []


# --- advisory semantics (D-025) ---------------------------------------------

async def test_a_cost_objection_reaches_the_next_orchestrator_decision(cases):
    """The other half of D-025: "its objection informs subsequent decisions".

    Without this the objection is decoration — it lands in the transcript, gets
    paraphrased into the summary by `hypothesis`, and the orchestrator never
    sees it as an opinion at all.
    """
    objection = {"objection": "UNIQUE_OBJECTION_MARKER_7", "would_change_management": False}
    script = [
        HYP, decide("order_test", "CBC"), objection,   # turn 0: objection raised
        HYP, decide("finalize"),                       # turn 1: must see it
        CHALLENGE, HYP, decide("finalize"), FINAL,
    ]
    graph, model, store = build_panel(cases, script)
    await run(graph, store)

    orchestrator_prompts = [p for p in model.rendered_prompts if "Choose exactly one action" in p]
    assert len(orchestrator_prompts) >= 2
    assert "## Cost review" in orchestrator_prompts[1], (
        "the cost-steward's objection never reached the orchestrator"
    )
    assert "UNIQUE_OBJECTION_MARKER_7" in orchestrator_prompts[1]
    # And it is one-shot: rendered once, not carried forever.
    assert "UNIQUE_OBJECTION_MARKER_7" not in orchestrator_prompts[0]


async def test_a_cost_objection_does_not_block_the_order(cases):
    """D-025: the sub-roles advise. The order proceeds regardless."""
    script = [HYP, decide("order_test", "CBC"), COST_OBJECT,
              HYP, decide("finalize"), CHALLENGE, HYP, decide("finalize"), FINAL]
    graph, _, store = build_panel(cases, script)
    out = await run(graph, store)
    assert any(e.kind == "test" and e.actor == "gatekeeper" for e in out["encounter_log"]), (
        "the test must still have been performed"
    )
    assert any(e.kind == "cost_objection" for e in out["encounter_log"])


async def test_a_cap_forced_finalize_skips_the_challenger(cases):
    """D-038: on the cap path the opinion would arrive too late to matter.

    Note what this does and does not prove. The cap path reaches `finalize`
    through `route_stop`, which never consults `route_action`, so no challenger
    is reachable there regardless of the code under test. This is a **regression
    guard against re-adding `challenger_stop`**, not a property of the router.
    """
    script = [HYP, decide("ask_patient", "q1"), HYP, decide("ask_patient", "q2"), FINAL]
    graph, _, store = build_panel(cases, script, max_turns=2)
    out = await run(graph, store)
    assert out["stop_reason"] == "turn_cap"
    assert out["final"] is not None
    assert not any(e.kind == "challenge" for e in out["encounter_log"])


# --- isolation, panel edition ------------------------------------------------

async def test_the_panel_orchestrator_never_sees_raw_event_text(cases):
    """The subgraph schema excludes `encounter_log`; this proves it in practice."""
    from graph_fixtures import ScriptedPatient

    case = next(c for c in cases if not c.dx_in_results and c.test_results)
    patient = ScriptedPatient(reply="UNIQUE_PATIENT_SENTENCE_XYZ")
    script = [HYP, decide("ask_patient", "q"),
              HYP, decide("finalize"), CHALLENGE, HYP, decide("finalize"), FINAL]
    graph, model, store = build_panel(cases, script, case_id=case.case_id, patient=patient)
    await run(graph, store, case.case_id)

    for prompt in (p for p in model.rendered_prompts if "Choose exactly one action" in p):
        assert "UNIQUE_PATIENT_SENTENCE_XYZ" not in prompt


async def test_the_challenger_never_sees_raw_event_text(cases):
    """It shares the subgraph's input schema, so it is bound by the same rule."""
    from graph_fixtures import ScriptedPatient

    patient = ScriptedPatient(reply="UNIQUE_PATIENT_SENTENCE_XYZ")
    script = [HYP, decide("ask_patient", "q"),
              HYP, decide("finalize"), CHALLENGE, HYP, decide("finalize"), FINAL]
    graph, model, store = build_panel(cases, script, patient=patient)
    await run(graph, store)
    for prompt in (p for p in model.rendered_prompts if "argue **against**" in p):
        assert "UNIQUE_PATIENT_SENTENCE_XYZ" not in prompt


async def test_ground_truth_never_reaches_a_panel_prompt_for_leak_free_cases(cases):
    """The string-scan requirement, applied to the panel graph."""
    for case in [c for c in cases if not c.dx_in_results and c.test_results][:4]:
        key = next(iter(case.test_results))
        script = [HYP, decide("order_test", key), COST_OK,
                  HYP, decide("finalize"), CHALLENGE, HYP, decide("finalize"), FINAL]
        graph, model, store = build_panel(cases, script, case_id=case.case_id)
        out = await run(graph, store, case.case_id)
        needle = case.correct_diagnosis.casefold()
        for prompt in model.rendered_prompts:
            assert needle not in prompt.casefold(), f"{case.case_id} leaked into a prompt"
        assert needle not in repr(out).casefold()


async def test_an_advisory_node_that_cannot_produce_output_does_not_kill_the_case(cases):
    """An opinion that fails is absent; it is not a lost case.

    The same failure in `single_doctor`'s orchestrator becomes a scored forced
    finalize, so letting it crash the panel would make the panel lose cases the
    single doctor keeps — a confound in the headline comparison, not just a bug.
    """
    script = [
        HYP, decide("ask_patient", "q1"),
        HYP, decide("ask_patient", "q2"),
        HYP, decide("ask_patient", "q3"),
        HYP,                                   # turn 3 -> challenger is due
        {"nonsense": 1}, {"nonsense": 2}, {"nonsense": 3},   # challenger fails 3x
        decide("finalize"),
        {"nonsense": 4}, {"nonsense": 5}, {"nonsense": 6},   # challenger_final fails 3x
        HYP, decide("finalize"), FINAL,
    ]
    graph, _, store = build_panel(cases, script)
    out = await run(graph, store)
    assert out["final"] is not None, "an advisory failure must not lose the case"
    assert out["parse_failures"] >= 3


async def test_a_dangerous_alternative_reaches_the_orchestrator_with_its_likelihood(cases):
    """D-051: the `medqa-0012` regression test.

    The panel lost that case by promoting the challenger's most-*dangerous*
    alternative to most-*likely* — "ranked highest because it is the most
    dangerous diagnosis if missed". The schema now forces the challenger to
    judge probability separately, and this asserts the orchestrator is actually
    shown that judgement rather than a bare danger.
    """
    danger = {"argument_against_leader": "The imaging is not specific.",
              "most_dangerous_unexcluded": "UNIQUE_DANGER_MARKER_9",
              "dangerous_alternative_likelihood": "less_likely"}
    script = [
        HYP, decide("order_test", "CBC"), COST_OK,
        HYP, decide("finalize"), danger,
        HYP, decide("finalize"), FINAL,
    ]
    graph, model, store = build_panel(cases, script)
    await run(graph, store)

    prompts = [p for p in model.rendered_prompts if "Choose exactly one action" in p]
    shown = [p for p in prompts if "UNIQUE_DANGER_MARKER_9" in p]
    assert shown, "the dangerous alternative never reached the orchestrator"
    p = shown[0]
    assert "LESS likely than your leader" in p, (
        "the danger was shown without its likelihood — exactly the conflation "
        "that lost medqa-0012"
    )
    assert "Severity and probability are separate." in p
