"""Command line interface.

The disclaimer prints on every invocation (brief §2), not just `--help`: this
simulation must never be mistaken for a clinical tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_budgets, load_dotenv, load_models, load_test_costs
from .data.loader import load_cases
from .data.splits import build_splits, select_eval_subset, write_splits

app = typer.Typer(add_completion=False, help="AgentClinic-from-scratch (educational simulation).")
console = Console()

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET = ROOT / "dataset" / "agentclinic_medqa_extended.jsonl"

DISCLAIMER = (
    "[bold]Educational simulation only.[/bold] Not medical advice. "
    "Must never be presented or used as medical advice, and must never "
    "process real patient data."
)


@app.callback()
def _main() -> None:
    console.print(Panel(DISCLAIMER, border_style="yellow", title="⚠"))
    # Loaded here rather than in each command so `play` and `run` behave the
    # same. Real environment variables take precedence over the file.
    loaded = load_dotenv()
    if loaded:
        console.print(f"[dim].env loaded: {', '.join(loaded)}[/dim]")


@app.command()
def dataset() -> None:
    """Summarise the downloaded dataset and its leakage flags."""
    cases = load_cases(DATASET)
    t = Table(title=f"{len(cases)} cases")
    t.add_column("property"); t.add_column("value", justify="right")
    t.add_row("cases", str(len(cases)))
    t.add_row("dx_in_results (diagnosis in test results)", str(sum(c.dx_in_results for c in cases)))
    t.add_row("dx_tokens_in_results", str(sum(c.dx_tokens_in_results for c in cases)))
    t.add_row("leak-free denominator", str(sum(not c.dx_in_results for c in cases)))
    t.add_row("empty Test_Results", str(sum(not c.has_test_results for c in cases)))
    console.print(t)


@app.command()
def splits(write: bool = typer.Option(False, help="Write dataset/splits.json (once only).")) -> None:
    """Build the grouped dev/held-out split and the fixed evaluation subset."""
    cases = load_cases(DATASET)
    s = build_splits(cases)
    subset = select_eval_subset(cases, s["dev"])
    console.print(f"dev={len(s['dev'])}  heldout={len(s['heldout'])}  eval subset={subset}")
    if write:
        write_splits(s, ROOT / "dataset" / "splits.json")
        console.print("[green]written to dataset/splits.json[/green]")


@app.command()
def config() -> None:
    """Show the resolved configuration, including derived values."""
    m, b, c = load_models(), load_budgets(), load_test_costs()
    t = Table(title="resolved config")
    t.add_column("setting"); t.add_column("value", justify="right")
    t.add_row("agent model (all roles)", m.for_role("patient"))
    t.add_row("judge", f"{m.judge.sdk}:{m.judge.model} (max {m.judge.max_calls_per_run}/run)")
    t.add_row("provider pin", f"{m.provider.pin} fallbacks={m.provider.allow_fallbacks}")
    t.add_row("max turns", str(b.max_turns))
    t.add_row("recursion limit (derived)", str(b.recursion_limit))
    t.add_row("spend caps", f"${b.spend_per_case_usd}/case  ${b.spend_per_run_usd}/run")
    t.add_row("requests", f"{b.rate_per_minute}/min  {b.requests_per_day}/day  x{b.concurrency}")
    t.add_row("unknown test price", f"${c.unknown_price}")
    console.print(t)


@app.command()
def play(
    case_id: str = typer.Argument(..., help="e.g. medqa-0002"),
    max_turns: int = typer.Option(20, help="turn cap for this encounter"),
    stub_patient: bool = typer.Option(
        False, "--stub-patient",
        help="Use a canned patient instead of a model (no OPENROUTER_API_KEY needed).",
    ),
) -> None:
    """Play the doctor against a real case, driving the real state machine."""
    import asyncio

    from langgraph.types import Command

    from .agents.gatekeeper import Gatekeeper
    from .agents.patient import Patient, PatientReply
    from .config import load_models, load_test_costs
    from .data.views import CaseStore
    from .graphs.interactive import INTERACTIVE_ACTIONS, build_interactive_graph
    from .graphs.state import new_state

    store = CaseStore(load_cases(DATASET))
    if case_id not in store.case_ids():
        console.print(f"[red]unknown case {case_id}[/red]")
        raise typer.Exit(1)

    class StubPatient:
        async def answer(self, question: str, *, case_id: str) -> PatientReply:
            return PatientReply(
                reply="(stub patient: run without --stub-patient for real answers)",
                unknown=True,
            )

    if stub_patient:
        patient: Any = StubPatient()
    else:
        from .llm.openrouter import (
        LLMCaller,
        MissingCredentials,
        UsageRecorder,
        build_chat_model,
    )

        models = load_models()
        recorder = UsageRecorder()
        model = build_chat_model(
            model=models.for_role("patient"),
            pin_provider=models.provider.pin,
            allow_fallbacks=models.provider.allow_fallbacks,
            attribution_title=models.provider.attribution_title,
        )
        patient = Patient(store.patient_view(case_id),
                          LLMCaller(model, recorder=recorder,
                                    structured_method=models.structured_output_method,
                       call_timeout=budgets.timeout_seconds))

    graph = build_interactive_graph(
        patient=patient,
        gatekeeper=Gatekeeper(store.gatekeeper_view(case_id), load_test_costs()),
        max_turns=max_turns,
        case_id=case_id,
    )
    cfg = {"configurable": {"thread_id": f"play-{case_id}"}}
    view = store.doctor_view(case_id)

    console.print(Panel(view.objective_for_doctor, title=f"Objective — {case_id}",
                        border_style="cyan"))
    console.print("[dim]actions: ask <question> | exam <region> | test <name> | "
                  "finalize | quit[/dim]\n")

    async def run() -> None:
        await graph.ainvoke(new_state(case_id, view.objective_for_doctor), cfg)
        shown = 1
        while True:
            state = (await graph.aget_state(cfg)).values
            for event in state["encounter_log"][shown:]:
                colour = {"patient": "green", "gatekeeper": "magenta"}.get(event.actor, "white")
                console.print(f"[{colour}]{event.actor}[/{colour}] {event.text}")
            shown = len(state["encounter_log"])

            if state.get("stop_reason"):
                console.print(f"\n[bold]encounter ended:[/bold] {state['stop_reason']} "
                              f"after {state['turn']} turns, "
                              f"simulated test cost ${state['test_cost_usd']:.2f}")
                return

            raw = console.input(f"[bold]turn {state['turn'] + 1}>[/bold] ").strip()
            if not raw or raw == "quit":
                return
            verb, _, argument = raw.partition(" ")
            action = {"ask": "ask_patient", "exam": "request_exam",
                      "test": "order_test", "finalize": "finalize"}.get(verb)
            if action is None or (action not in INTERACTIVE_ACTIONS and action != "finalize"):
                console.print("[yellow]use: ask / exam / test / finalize / quit[/yellow]")
                continue
            await graph.ainvoke(Command(resume={"action": action, "argument": argument}), cfg)

    asyncio.run(run())


@app.command()
def run(
    split: str = typer.Option("dev", help="dev or heldout (heldout needs the explicit flag)"),
    limit: int = typer.Option(3, help="number of cases; the fixed subset is 3 (D-022)"),
    max_turns: int = typer.Option(20),
    no_report: bool = typer.Option(False, "--no-report", help="skip report.md/results.csv"),
    cache: bool = typer.Option(False, "--cache", help="allow the response cache (dev only)"),
) -> None:
    """Run the single-doctor configuration over the evaluation subset."""
    import asyncio
    import json
    import uuid

    from .agents.gatekeeper import Gatekeeper, make_llm_disambiguator
    from .agents.judge import Judge
    from .agents.patient import Patient
    from .config import load_budgets, load_dotenv, load_models, load_test_costs
    from .data.splits import select_eval_subset
    from .data.views import CaseStore
    from .eval.report import RunMetadata, render, write_report
    from .eval.runner import run_evaluation, write_finals, write_results_csv
    from .graphs.schemas import make_orchestrator_decision
    from .graphs.single_doctor import build_single_doctor_graph
    from .llm.guards import DailyRequestCounter, RunGuards, SpendTracker, TokenBucket
    from .llm.openrouter import (
        LLMCaller,
        MissingCredentials,
        UsageRecorder,
        build_chat_model,
    )
    from .tracing import Tracer

    # Q-34: the cache is hard-disabled for any run that writes a report. This is
    # an assertion, not a flag default, so it cannot be turned off by accident.
    if cache and not no_report:
        console.print("[red]--cache requires --no-report: a reported run must not "
                      "replay cached completions.[/red]")
        raise typer.Exit(2)

    models, budgets, costs = load_models(), load_budgets(max_turns=max_turns), load_test_costs()
    cases = load_cases(DATASET)
    store = CaseStore(cases)

    splits_path = ROOT / "dataset" / "splits.json"
    if not splits_path.exists():
        console.print("[red]dataset/splits.json missing — run `splits --write` first[/red]")
        raise typer.Exit(1)
    splits = json.loads(splits_path.read_text())
    if split == "heldout":
        console.print("[yellow]running on the HELD-OUT split[/yellow]")
    subset = (select_eval_subset(cases, splits["dev"], n=limit) if split == "dev"
              else sorted(splits["heldout"])[:limit])
    selected = [c for c in cases if c.case_id in set(subset)]
    console.print(f"cases: {[c.case_id for c in selected]}")

    run_id = f"{split}-single_doctor-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "runs" / run_id
    tracer = Tracer(run_dir=run_dir)

    guards = RunGuards(
        bucket=TokenBucket(rate_per_minute=budgets.rate_per_minute,
                           capacity=budgets.rate_per_minute),
        daily=DailyRequestCounter(ROOT / "runs" / ".daily.json", limit=budgets.requests_per_day),
        spend=SpendTracker(per_case_cap=budgets.spend_per_case_usd,
                           per_run_cap=budgets.spend_per_run_usd),
    )
    remaining = guards.daily.remaining()
    # Pre-flight. ~3 calls per turn (hypothesis, orchestrator, patient/gatekeeper)
    # plus a finalize. Refusing up front is far better than discovering the cap
    # mid-run, where a daily-cap 429 turns every remaining case into an `error`
    # and silently shrinks the accuracy denominator.
    projected = len(selected) * (max_turns * 3 + 2)
    console.print(f"daily requests remaining: {remaining}  projected: ~{projected}")
    if projected > remaining:
        console.print(
            f"[red]refusing to start: this run needs roughly {projected} requests but "
            f"only {remaining} remain in today's allowance.[/red]\n"
            "[dim]reduce --limit or --max-turns, or wait for the 00:00 UTC reset[/dim]"
        )
        raise typer.Exit(2)

    enabled = frozenset({"ask_patient", "request_exam", "order_test"})
    decision_model = make_orchestrator_decision(enabled)
    recorder = UsageRecorder()
    try:
        chat = build_chat_model(
            model=models.for_role("orchestrator"),
            pin_provider=None if (cache and no_report) else models.provider.pin,
            allow_fallbacks=models.provider.allow_fallbacks,
            attribution_title=models.provider.attribution_title,
            timeout=budgets.timeout_seconds,
            # 0: LLMCaller owns retries. SDK-internal retries would multiply
            # the wall-clock deadline by (max_retries + 1) invisibly.
            max_retries=0,
        )  # callbacks are attached per invocation by LLMCaller
    except MissingCredentials as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[dim]export OPENROUTER_API_KEY=... and try again[/dim]")
        raise typer.Exit(1) from None
    caller = LLMCaller(chat, guards=guards, recorder=recorder, tracer=tracer,
                       structured_method=models.structured_output_method,
                       call_timeout=budgets.timeout_seconds)
    judge = Judge(model=models.judge.model,
                  max_calls_per_run=models.judge.max_calls_per_run, tracer=tracer)

    def build_graph(case_id: str) -> Any:
        return build_single_doctor_graph(
            caller=caller,
            patient=Patient(store.patient_view(case_id), caller),
            gatekeeper=Gatekeeper(
                store.gatekeeper_view(case_id), costs,
                # Without this the cascade stops at exact + synonyms, and every
                # other request becomes a fabricated "not available".
                llm_disambiguate=make_llm_disambiguator(caller, case_id),
            ),
            case_id=case_id, decision_model=decision_model, enabled=enabled,
            max_turns=budgets.max_turns, guards=guards,
        )

    finals: dict[str, Any] = {}
    results = asyncio.run(run_evaluation(
        cases=selected, store=store, build_graph=build_graph, judge=judge,
        recursion_limit=budgets.recursion_limit, concurrency=budgets.concurrency,
        tracer=tracer, guards=guards, caller=caller, finals=finals,
        # Generous, but finite: a case that cannot finish in this long is stuck.
        case_deadline_s=budgets.max_turns * 6 * budgets.timeout_seconds,
    ))
    # Written even for --no-report: the encounters are the expensive half, and
    # keeping their answers is what makes `judge <run_id>` cheap.
    write_finals(finals, run_dir / "finals.json")

    for r in results:
        console.print(f"  {r.case_id}  {r.outcome:9s} {r.match_type or '—':9s} "
                      f"turns={r.turns} stop={r.stop_reason}")

    if no_report:
        console.print(f"[dim]--no-report: traces only, at {run_dir}[/dim]")
        return

    meta = RunMetadata(
        run_id=run_id, config_name="single_doctor", model=models.for_role("orchestrator"),
        judge_model=models.judge.model, provider_pin=",".join(models.provider.pin),
        fallbacks=models.provider.allow_fallbacks, cache_enabled=cache,
        structured_output_mode=models.structured_output_method, enabled_actions=tuple(sorted(enabled)),
        max_turns=budgets.max_turns, split=split,
    )
    write_report(render(results, meta), run_dir / "report.md")
    write_results_csv(results, run_dir / "results.csv")
    console.print(f"[green]report: {run_dir / 'report.md'}[/green]")


@app.command()
def judge(run_id: str) -> None:
    """Judge a completed run's stored answers, without re-running encounters.

    Judging needs an Anthropic credential; encounters need ~180 OpenRouter
    requests. Separating them means a missing `ant auth login` costs three judge
    calls to recover from rather than a whole re-run.
    """
    import asyncio
    import csv
    import json

    from .agents.judge import Judge, top_k
    from .config import load_models
    from .data.views import CaseStore
    from .eval.report import RunMetadata, render, write_report
    from .eval.runner import CaseResult, write_results_csv
    from .graphs.schemas import FinalAnswer
    from .tracing import Tracer

    run_dir = ROOT / "runs" / run_id
    finals_path, results_path = run_dir / "finals.json", run_dir / "results.csv"
    if not finals_path.exists() or not results_path.exists():
        console.print(f"[red]{run_dir} has no finals.json/results.csv to judge[/red]")
        raise typer.Exit(1)

    finals = json.loads(finals_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(results_path.open(encoding="utf-8")))
    store = CaseStore(load_cases(DATASET))
    models = load_models()
    tracer = Tracer(run_dir=run_dir)
    judge_agent = Judge(model=models.judge.model,
                        max_calls_per_run=models.judge.max_calls_per_run, tracer=tracer)

    async def main() -> list[CaseResult]:
        out: list[CaseResult] = []
        for row in rows:
            r = CaseResult(case_id=row["case_id"], outcome=row["outcome"])
            for key, cast in (("stop_reason", str), ("diagnosis", str), ("match_tiers", str)):
                setattr(r, key, cast(row.get(key) or ""))
            for key in ("turns", "patient_questions", "tests_ordered", "exams_requested",
                        "unlisted_tests", "parse_failures"):
                setattr(r, key, int(float(row.get(key) or 0)))
            for key in ("test_cost_usd", "api_cost_usd", "final_confidence", "latency_s"):
                setattr(r, key, float(row.get(key) or 0))
            r.forced_stop = (row.get("forced_stop") or "").lower() == "true"
            r.dx_in_results = (row.get("dx_in_results") or "").lower() == "true"
            r.dx_tokens_in_results = (row.get("dx_tokens_in_results") or "").lower() == "true"
            r.abstained = (row.get("abstained") or "").lower() == "true"
            r.match_tiers = dict(
                part.split("=") for part in (row.get("match_tiers") or "").split(";") if "=" in part
            )
            r.match_tiers = {k: int(v) for k, v in r.match_tiers.items()}

            answer = finals.get(r.case_id)
            if answer is None or r.abstained:
                # An abstention is never judged (D-028), and a crash left no answer.
                out.append(r)
                continue
            final = FinalAnswer.model_validate(answer)
            try:
                verdict = await judge_agent.verdict(
                    case_id=r.case_id, final=final, view=store.judge_view(r.case_id))
            except Exception as exc:  # noqa: BLE001
                r.outcome = "error"
                r.error = f"judge failed: {type(exc).__name__}"
                tracer.judge(case_id=r.case_id, event="judge_error",
                             error_type=type(exc).__name__, error_message=str(exc)[:500])
                console.print(f"  {r.case_id}  [red]judge failed: {type(exc).__name__}[/red]")
                out.append(r)
                continue
            r.outcome = "scored"
            r.error = None
            r.diagnosis = final.diagnosis
            r.differential = [d.diagnosis for d in final.differential]
            r.match_type = verdict.match_type
            r.judge_correct = verdict.correct
            r.lenient_correct = verdict.lenient_correct
            r.top_1, r.top_3, r.top_5 = top_k(verdict, 1), top_k(verdict, 3), top_k(verdict, 5)
            r.in_differential = any(verdict.entry_matches)
            console.print(f"  {r.case_id}  {verdict.match_type:9s} "
                          f"correct={verdict.correct} top1={r.top_1}")
            out.append(r)
        return out

    results = asyncio.run(main())
    meta = RunMetadata(
        run_id=run_id, config_name="single_doctor", model=models.for_role("orchestrator"),
        judge_model=models.judge.model, provider_pin=",".join(models.provider.pin),
        fallbacks=models.provider.allow_fallbacks, cache_enabled=False,
        structured_output_mode=models.structured_output_method,
        enabled_actions=("ask_patient", "request_exam", "order_test"),
        max_turns=20, split="dev",
    )
    write_report(render(results, meta), run_dir / "report.md")
    write_results_csv(results, results_path)
    console.print(f"[green]re-judged: {run_dir / 'report.md'}[/green]")


@app.command()
def trace(run_id: str, case_id: str) -> None:
    """Render a case trace (brief §7's CLI trace viewer)."""
    path = ROOT / "runs" / run_id / "traces" / f"{case_id}.jsonl"
    if not path.exists():
        console.print(f"[red]no trace at {path}[/red]")
        raise typer.Exit(1)
    t = Table(title=f"{run_id} / {case_id}")
    for col in ("ts", "kind", "node", "detail", "tok", "cost", "s"):
        t.add_column(col, overflow="fold")
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        detail = r.get("event") or r.get("tool") or r.get("error_message") or ""
        tok = (f"{r.get('prompt_tokens','')}/{r.get('completion_tokens','')}"
               if r["kind"] == "llm_call" else "")
        cost = f"{r['cost']:.5f}" if r.get("cost") else ""
        t.add_row(r["ts"][11:23], r["kind"], str(r.get("node", "")), str(detail)[:70],
                  tok, cost, str(r.get("latency_s", "")))
    console.print(t)


if __name__ == "__main__":
    app()
