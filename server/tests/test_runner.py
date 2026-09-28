"""The eval runner: outcome mapping, crash containment, judging."""

from __future__ import annotations

import csv

import pytest

from agentclinic.agents.judge import Judge
from agentclinic.data.views import CaseStore
from agentclinic.eval.report import RunMetadata, render
from agentclinic.eval.runner import run_case, run_evaluation, write_results_csv
from agentclinic.graphs.schemas import JudgeVerdict
from graph_fixtures import FINAL, HYP, build, decide

RECURSION = 100


class FakeJudge(Judge):
    def __init__(self, match_type="exact", entry_matches=(True,)):
        super().__init__(client=object())
        self._verdict = JudgeVerdict(match_type=match_type,
                                     entry_matches=list(entry_matches), reasoning="r")

    async def verdict(self, *, case_id, final, view):
        self.calls += 1
        return self._verdict


def graph_factory(cases, script, case_id="medqa-0010", **kw):
    graph, model, store = build(cases, script, case_id=case_id, **kw)
    return (lambda _cid: graph), store


async def test_a_completed_encounter_is_scored_and_judged(cases):
    case = next(c for c in cases if c.case_id == "medqa-0010")
    factory, store = graph_factory(cases, [HYP, decide("finalize"), FINAL])
    judge = FakeJudge()
    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=judge, recursion_limit=RECURSION)
    assert result.outcome == "scored"
    assert result.judge_correct and result.top_1 and result.match_type == "exact"
    assert judge.calls == 1


async def test_abstention_is_not_judged_and_leaves_the_denominator(cases):
    """D-028: the judge is not called, and the case is excluded from accuracy."""
    case = next(c for c in cases if c.case_id == "medqa-0010")
    abstain = {**FINAL, "diagnosis": "", "abstain": True, "confidence": 0.2}
    factory, store = graph_factory(cases, [HYP, decide("finalize"), abstain])
    judge = FakeJudge()
    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=judge, recursion_limit=RECURSION)
    assert result.outcome == "abstained" and judge.calls == 0


async def test_parse_failure_is_error_not_abstention(cases):
    """A JSON formatting failure is a harness fault, not clinical uncertainty."""
    case = next(c for c in cases if c.case_id == "medqa-0010")
    factory, store = graph_factory(cases, [HYP, {"bad": 1}, {"bad": 2}, {"bad": 3}])
    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=FakeJudge(), recursion_limit=RECURSION)
    assert result.outcome == "error" and result.stop_reason == "parse_failure"


async def test_a_crash_becomes_a_result_rather_than_an_exception(cases):
    """One bad case must not take the run down."""
    case = next(c for c in cases if c.case_id == "medqa-0010")

    class Exploding:
        async def astream(self, *a, **kw):
            raise RuntimeError("boom: prompt was ...")
            yield  # pragma: no cover — makes this an async generator

    result = await run_case(case=case, store=CaseStore(cases),
                            build_graph=lambda _c: Exploding(),
                            judge=None, recursion_limit=RECURSION)
    assert result.outcome == "crash"
    assert result.error.startswith("RuntimeError")
    assert "Traceback" not in (result.error or "")


async def test_forced_stop_is_derived_from_the_stop_reason(cases):
    case = next(c for c in cases if c.case_id == "medqa-0010")
    # HYP before FINAL: the hypothesis pass that reads the last result on a cap (M-07).
    script = [HYP, decide("ask_patient", "q"), HYP, decide("ask_patient", "q2"), HYP, FINAL]
    factory, store = graph_factory(cases, script, max_turns=2)
    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=FakeJudge(), recursion_limit=RECURSION)
    assert result.stop_reason == "turn_cap" and result.forced_stop is True


async def test_leakage_flags_travel_onto_the_result(cases):
    case = next(c for c in cases if c.dx_in_results)
    factory, store = graph_factory(cases, [HYP, decide("finalize"), FINAL],
                                   case_id=case.case_id)
    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=FakeJudge(), recursion_limit=RECURSION)
    assert result.dx_in_results is True


async def test_results_csv_round_trips(cases, tmp_path):
    case = next(c for c in cases if c.case_id == "medqa-0010")
    factory, store = graph_factory(cases, [HYP, decide("finalize"), FINAL])
    results = [await run_case(case=case, store=store, build_graph=factory,
                              judge=FakeJudge(), recursion_limit=RECURSION)]
    path = tmp_path / "results.csv"
    write_results_csv(results, path)
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["case_id"] == "medqa-0010" and rows[0]["outcome"] == "scored"


def test_report_never_prints_an_accuracy_without_a_denominator(cases):
    from agentclinic.eval.runner import CaseResult

    meta = RunMetadata(run_id="r", config_name="single_doctor", model="m",
                       judge_model="j", provider_pin="Nvidia", fallbacks=False,
                       cache_enabled=False, structured_output_mode="strict",
                       enabled_actions=("ask_patient",), max_turns=20, split="dev")
    text = render([CaseResult(case_id="c1", outcome="abstained", abstained=True)], meta)
    assert "n/a (n_scored=0)" in text
    assert "0.000 (n_scored=0)" not in text
    assert "validates the harness; it does not measure anything" in text


def test_a_rebuilt_run_does_not_report_unrecorded_telemetry_as_zero():
    """D-050's counterpart. The rebuild cannot recover encounter events.

    Printing the dataclass defaults would turn "never recorded" into "observed
    zero" — the panel run's report claimed `exams: 0`, `unlisted requests: 0`
    and `match tiers: (none)` for 25 tests it had no event records for.
    """
    from agentclinic.eval.runner import CaseResult

    meta = RunMetadata(run_id="r", config_name="panel", model="m",
                       judge_model="j", provider_pin="Novita", fallbacks=False,
                       cache_enabled=False, structured_output_mode="function_calling",
                       enabled_actions=("ask_patient", "order_test"), max_turns=20,
                       split="dev")
    rebuilt = CaseResult(case_id="c1", outcome="scored", judge_correct=True,
                         turns=18, tests_ordered=9, behaviour_recovered=False)
    text = render([rebuilt], meta)
    assert "- gatekeeper match tiers: n/a (not recorded)" in text
    assert "(none)" not in text
    assert "exams: n/a (not recorded)" in text
    assert "- unlisted requests: n/a (not recorded)" in text
    assert "lower bound" in text
    assert "reconstructed from `finals.json`" in text

    # A normally-measured run keeps reporting real numbers.
    live = CaseResult(case_id="c1", outcome="scored", judge_correct=True,
                      turns=18, tests_ordered=9, exams_requested=0)
    live_text = render([live], meta)
    assert "n/a (not recorded)" not in live_text
    assert "exams: 0" in live_text
    assert "lower bound" not in live_text


async def test_a_judge_failure_is_contained_to_one_case(cases):
    """A judge outage must not destroy a run whose encounters are already paid for."""
    case = next(c for c in cases if c.case_id == "medqa-0010")
    factory, store = graph_factory(cases, [HYP, decide("finalize"), FINAL])

    class BrokenJudge(FakeJudge):
        async def verdict(self, *, case_id, final, view):
            raise RuntimeError("judge prompt was: <contains ground truth>")

    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=BrokenJudge(), recursion_limit=RECURSION)
    assert result.outcome == "error"
    assert result.error == "judge failed: RuntimeError"


async def test_a_judge_failure_message_never_reaches_results_csv(cases, tmp_path):
    """The judge prompt contains ground truth, so only the exception type may be recorded.

    Uses a case whose ground truth differs from the fixture's submitted answer —
    otherwise the string would appear in the CSV legitimately, as the doctor's
    own diagnosis, and the test would fail for the wrong reason.
    """
    case = next(c for c in cases
                if c.correct_diagnosis.casefold() != FINAL["diagnosis"].casefold()
                and len(c.correct_diagnosis) > 12)
    factory, store = graph_factory(cases, [HYP, decide("finalize"), FINAL],
                                   case_id=case.case_id)

    class LeakyJudge(FakeJudge):
        async def verdict(self, *, case_id, final, view):
            raise RuntimeError(f"failed while judging against {view.correct_diagnosis}")

    result = await run_case(case=case, store=store, build_graph=factory,
                            judge=LeakyJudge(), recursion_limit=RECURSION)
    write_results_csv([result], tmp_path / "results.csv")
    text = (tmp_path / "results.csv").read_text()
    assert case.correct_diagnosis.casefold() not in text.casefold()


async def test_one_exploding_case_does_not_cancel_its_siblings(cases):
    """`gather` with return_exceptions, plus per-case containment."""
    selected = [c for c in cases if c.case_id in {"medqa-0002", "medqa-0009"}]
    good_graph, _, store = build(cases, [HYP, decide("finalize"), FINAL],
                                 case_id="medqa-0009")

    class Exploding:
        async def ainvoke(self, *a, **kw):
            raise RuntimeError("boom")

    def factory(case_id):
        return Exploding() if case_id == "medqa-0002" else good_graph

    results = await run_evaluation(cases=selected, store=store, build_graph=factory,
                                   judge=FakeJudge(), recursion_limit=RECURSION,
                                   concurrency=2)
    by_id = {r.case_id: r for r in results}
    assert by_id["medqa-0002"].outcome == "crash"
    assert by_id["medqa-0009"].outcome == "scored"


async def test_a_hanging_call_is_bounded_by_the_case_deadline(cases):
    """The bug this guards: httpx timeouts are PER-OPERATION, not total.

    A provider trickling bytes resets the read timer forever. Observed live —
    a run held two ESTABLISHED sockets for 65 minutes with no progress, because
    the turn cap bounds turns and nothing bounded wall-clock.
    """
    import asyncio

    case = next(c for c in cases if c.case_id == "medqa-0010")

    class Hanging:
        async def astream(self, *a, **kw):
            await asyncio.sleep(3600)
            yield  # pragma: no cover — makes this an async generator

    result = await run_case(case=case, store=CaseStore(cases),
                            build_graph=lambda _c: Hanging(), judge=None,
                            recursion_limit=RECURSION, case_deadline_s=0.2)
    assert result.outcome == "crash"
    assert "TimeoutError" in (result.error or "")


async def test_a_call_timeout_is_treated_as_transient_not_as_bad_content(cases):
    """Re-prompting a provider that is not answering is pointless; waiting is not."""
    import asyncio

    from agentclinic.llm.openrouter import _looks_empty

    assert _looks_empty(asyncio.TimeoutError())
    assert _looks_empty(TimeoutError())
    assert not _looks_empty(ValueError("field required"))


def test_judge_refuses_api_key_billing_when_configured_for_subscription(monkeypatch):
    """D-046: the SDK prefers an API key over the OAuth profile, silently.

    Without this guard, an ANTHROPIC_API_KEY appearing in the environment would
    quietly switch the judge from subscription auth to per-token billing, and the
    first sign would be an invoice.
    """
    from agentclinic.agents.judge import Judge, JudgeAuthMode

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    judge = Judge(require_subscription=True)
    assert judge.auth_mode() == "api_key"
    with pytest.raises(JudgeAuthMode, match="configured for subscription auth"):
        judge._ensure_client()


def test_judge_allows_api_key_when_explicitly_permitted(monkeypatch):
    """The refusal is a policy, not a prohibition — it can be opted out of."""
    from agentclinic.agents.judge import Judge

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    judge = Judge(require_subscription=False)
    assert judge._ensure_client() is not None


def test_judge_reports_a_missing_credential_clearly(monkeypatch):
    from agentclinic.agents.judge import Judge, JudgeAuthMode

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    judge = Judge(require_subscription=True)
    assert judge.auth_mode() == "none"
    with pytest.raises(JudgeAuthMode, match="ant auth login"):
        judge._ensure_client()
