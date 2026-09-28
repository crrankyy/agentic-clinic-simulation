"""Command line interface.

The disclaimer prints on every invocation (brief §2), not just `--help`: this
simulation must never be mistaken for a clinical tool.
"""

from __future__ import annotations

import asyncio
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

from .paths import DATASET_FILE as DATASET, REPO_ROOT as ROOT

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
    t.add_row("agent model (all roles)", m.model)
    t.add_row("judge", f"{m.judge.model} (max {m.judge.max_calls_per_run}/run)")
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
        async def answer(self, question: str, *, case_id: str, history=()) -> PatientReply:
            return PatientReply(
                reply="(stub patient: run without --stub-patient for real answers)",
                unknown=True,
            )

    if stub_patient:
        patient: Any = StubPatient()
    else:
        from .llm.factory import build_caller

        patient = Patient(store.patient_view(case_id),
                          build_caller(load_models(), budgets, guards=None))

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
    config: str = typer.Option("single_doctor", help="single_doctor or panel"),
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

    from .agents.judge import Judge
    from .config import load_budgets, load_dotenv, load_models, load_test_costs
    from .data.splits import select_eval_subset
    from .data.views import CaseStore
    from .eval.report import RunMetadata, render, write_report
    from .eval.runner import run_evaluation, write_finals, write_results_csv
    from .graphs.encounter import ENABLED_ACTIONS, build_case_graph
    from .llm.openrouter import MissingCredentials
    from .tracing import Tracer

    # Q-34: the cache is hard-disabled for any run that writes a report. This is
    # an assertion, not a flag default, so it cannot be turned off by accident.
    if config not in {"single_doctor", "panel"}:
        console.print(f"[red]unknown config {config!r}: use single_doctor or panel[/red]")
        raise typer.Exit(2)
    if cache and not no_report:
        console.print("[red]--cache requires --no-report: a reported run must not "
                      "replay cached completions.[/red]")
        raise typer.Exit(2)

    models = load_models()
    budgets = load_budgets(max_turns=max_turns, graph=config)
    costs = load_test_costs()
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

    run_id = f"{split}-{config}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "runs" / run_id
    tracer = Tracer(run_dir=run_dir)
    from .eval.runmeta import write_run_meta

    write_run_meta(run_dir, config=config, models=models, split=split,
                   max_turns=max_turns, cases=[c.case_id for c in selected])

    from .llm.factory import build_caller, build_guards, projected_requests
    from .llm.preflight import preflight

    # M-17: a revoked key or an unserved model is found here, for free, rather
    # than by the first case -- which used to report it as a schema failure.
    if not (cache and no_report):
        check = asyncio.run(preflight(models, routing=True))
        if not check.ok:
            console.print(f"[red]refusing to start: {check.reason}[/red]")
            raise typer.Exit(2)
        if check.key_limit_remaining is not None:
            console.print(f"key spend remaining: ${check.key_limit_remaining:.2f}")

    guards = build_guards(models, budgets, ROOT / "runs" / ".daily.json")
    remaining = guards.daily.remaining()
    # Pre-flight. Refusing up front is far better than discovering the cap
    # mid-run, where a daily-cap 429 turns every remaining case into an `error`
    # and silently shrinks the accuracy denominator. Worst case, measured on a
    # scripted model: single_doctor 41 calls at 20 turns; panel 127 when every
    # turn orders a test AND is re-deliberated. Free tier only (D-062).
    projected = len(selected) * projected_requests(config, max_turns)
    if remaining is not None:
        console.print(f"daily requests remaining: {remaining}  projected: ~{projected}")
        if projected > remaining:
            console.print(
                f"[red]refusing to start: this run needs roughly {projected} requests but "
                f"only {remaining} remain in today's allowance.[/red]\n"
                "[dim]reduce --limit or --max-turns, or wait for the 00:00 UTC reset[/dim]"
            )
            raise typer.Exit(2)
    else:
        console.print(f"paid model: no request allowance; spend capped at "
                      f"${budgets.spend_per_case_usd}/case, ${budgets.spend_per_run_usd}/run")

    try:
        caller = build_caller(models, budgets, guards=guards, tracer=tracer,
                              unpinned=bool(cache and no_report))
    except MissingCredentials as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[dim]export OPENROUTER_API_KEY=... and try again[/dim]")
        raise typer.Exit(1) from None
    judge = Judge(model=models.judge.model,
                  max_calls_per_run=models.judge.max_calls_per_run, tracer=tracer,
                  require_subscription=models.judge.auth == "subscription")

    def build_graph(case_id: str) -> Any:
        return build_case_graph(store, case_id, config=config, caller=caller, guards=guards,
                                costs=costs, max_turns=budgets.max_turns,
                                question_overlap=budgets.question_repeat_overlap)

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
        run_id=run_id, config_name=config, model=models.model,
        judge_model=models.judge.model,
        provider_pin=",".join(models.provider.pin),
        fallbacks=models.provider.allow_fallbacks, cache_enabled=cache,
        structured_output_mode=models.structured_output_method,
        enabled_actions=tuple(sorted(ENABLED_ACTIONS)),
        max_turns=budgets.max_turns, split=split,
    )
    write_report(render(results, meta), run_dir / "report.md")
    write_results_csv(results, run_dir / "results.csv")
    console.print(f"[green]report: {run_dir / 'report.md'}[/green]")


@app.command()
def judge(
    run_id: str,
    verdicts: Path = typer.Option(
        None, "--verdicts",
        help="JSON of hand-assigned verdicts; skips the model entirely.",
    ),
) -> None:
    """Judge a completed run's stored answers, without re-running encounters.

    Judging needs an Anthropic credential; encounters need ~180 OpenRouter
    requests. Separating them means a missing `ant auth login` costs three judge
    calls to recover from rather than a whole re-run.
    """
    import asyncio
    import csv
    import json

    from .agents.judge import Judge
    from .config import load_models
    from .data.views import CaseStore
    from .eval.report import RunMetadata, render, write_report
    from .eval.runner import CaseResult, apply_verdict, result_from_row, write_results_csv
    from .graphs.schemas import FinalAnswer, JudgeVerdict
    from .tracing import Tracer

    run_dir = ROOT / "runs" / run_id
    finals_path, results_path = run_dir / "finals.json", run_dir / "results.csv"
    if not finals_path.exists():
        console.print(f"[red]{run_dir} has no finals.json to judge[/red]")
        raise typer.Exit(1)

    finals = json.loads(finals_path.read_text(encoding="utf-8"))
    # A hand-assigned verdict file replaces the model. Defensible only at very
    # small n, and the report says so rather than presenting these as automated.
    manual: dict[str, Any] = {}
    if verdicts is not None:
        manual = json.loads(Path(verdicts).read_text(encoding="utf-8"))
        console.print(f"[yellow]manual verdicts: {len(manual)} cases, no model call[/yellow]")
    all_cases = load_cases(DATASET)
    store = CaseStore(all_cases)
    if results_path.exists():
        rows = list(csv.DictReader(results_path.open(encoding="utf-8")))
    else:
        # The encounters completed but the CSV was never written -- a crash in
        # report generation, say. Recover rather than re-run: the answers are
        # the expensive part and they survived.
        from .eval.runner import rebuild_results

        rebuilt = rebuild_results(run_dir, {c.case_id: c for c in all_cases})
        console.print(f"[yellow]results.csv missing — rebuilt {len(rebuilt)} rows "
                      "from finals.json and traces[/yellow]")
        rows = [r.row() for r in rebuilt]
    models = load_models()
    tracer = Tracer(run_dir=run_dir)
    judge_agent = Judge(model=models.judge.model,
                        max_calls_per_run=models.judge.max_calls_per_run, tracer=tracer,
                        require_subscription=models.judge.auth == "subscription")
    if not manual:
        console.print(f"[dim]judge auth: {judge_agent.auth_mode()}[/dim]")

    async def main() -> list[CaseResult]:
        out: list[CaseResult] = []
        for row in rows:
            # Every field, coerced by its declared type (result_from_row).
            r = result_from_row(row)
            out.append(r)
            answer = finals.get(r.case_id)
            if answer is None or r.abstained:
                # An abstention is never judged (D-028), and a crash left no answer.
                continue
            final = FinalAnswer.model_validate(answer)
            if r.case_id in manual:
                verdict = JudgeVerdict.model_validate(manual[r.case_id])
                tracer.judge(case_id=r.case_id, source="manual",
                             match_type=verdict.match_type, correct=verdict.correct,
                             reasoning=verdict.reasoning)
            else:
                try:
                    verdict = await judge_agent.verdict(
                        case_id=r.case_id, final=final, view=store.judge_view(r.case_id))
                except Exception as exc:  # noqa: BLE001
                    r.outcome = "error"
                    r.error = f"judge failed: {type(exc).__name__}"
                    tracer.judge(case_id=r.case_id, event="judge_error",
                                 error_type=type(exc).__name__, error_message=str(exc)[:500])
                    console.print(f"  {r.case_id}  [red]judge failed: {type(exc).__name__}[/red]")
                    continue
            apply_verdict(r, final, verdict)
            console.print(f"  {r.case_id}  {verdict.match_type:9s} correct={verdict.correct} "
                          f"top1={r.top_1}" + ("  [dim](manual)[/dim]" if r.case_id in manual else ""))
        return out

    results = asyncio.run(main())
    # The run id encodes the configuration (`dev-panel-abc123`). Reading it back
    # avoids referring to the module-level `config` command, which is what the
    # bare name resolves to in this scope.
    parts = run_id.split("-")
    config_name = parts[1] if len(parts) > 2 else "single_doctor"
    meta = RunMetadata(
        run_id=run_id, config_name=config_name, model=models.model,
        judge_model=("manual (hand-assigned, not a model)" if manual
                     else models.judge.model),
        provider_pin=",".join(models.provider.pin),
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


@app.command()
def probe(
    providers: str = typer.Option("", help="comma-separated providers to try; "
                                           "default: the configured pin"),
    calls: int = typer.Option(2, help="calls per schema per provider"),
) -> None:
    """Check the configured model on the REAL nested schemas, per provider.

    Lesson from nemotron (PHASE_3_NOTES 7.1): a flat schema can pass while the
    nested HypothesisUpdate fails 0/2, so a model is only usable once it has
    produced the schemas this pipeline actually asks for. Costs a few calls.
    """
    import time

    from .config import load_budgets, load_dotenv, load_models
    from .graphs.schemas import HypothesisUpdate, make_orchestrator_decision
    from .llm.factory import build_caller
    from .llm.preflight import preflight

    load_dotenv()
    models = load_models()
    budgets = load_budgets()
    check = asyncio.run(preflight(models))
    console.print(f"preflight: {'ok' if check.ok else 'FAILED'} -- {check.reason}")
    if check.key_limit_remaining is not None:
        console.print(f"key spend remaining: ${check.key_limit_remaining:.2f}")
    if not check.ok and "rejected the API key" in check.reason:
        raise typer.Exit(2)

    candidates = [p.strip() for p in providers.split(",") if p.strip()] or list(models.provider.pin)
    prompt = (
        "You are a physician maintaining a working differential.\n\n## Referral\n"
        "Evaluate and diagnose the patient presenting with gait and limb ataxia.\n\n"
        "## What has happened so far\n"
        "[turn 1] doctor: Tell me about your medical history and medications.\n"
        "[turn 1] patient: I have Crohn disease and have been on natalizumab for a year.\n"
        "[turn 2] doctor: MRI brain with contrast\n"
        "[turn 2] gatekeeper: MRI Brain: multifocal demyelinating lesions without "
        "enhancement.\n\nReturn your updated differential and a short summary."
    )
    decision = make_orchestrator_decision(frozenset({"ask_patient", "order_test"}))
    for provider in candidates:
        caller = build_caller(models, budgets, guards=None, pin_override=[provider])
        console.print(f"\n[bold]{models.model} @ {provider}[/bold]")
        for label, schema, node, extra in (
            ("HypothesisUpdate (nested)", HypothesisUpdate, "hypothesis", ""),
            ("OrchestratorDecision", decision, "orchestrator",
             "\n\nNow choose exactly one next action."),
        ):
            for i in range(calls):
                t0 = time.monotonic()
                try:
                    out = asyncio.run(caller.structured(schema, prompt + extra,
                                                        case_id="probe", node=node))
                    detail = (f"differential={len(out.differential)} "
                              f"top={out.differential[0].diagnosis!r}"
                              if hasattr(out, "differential") and out.differential
                              else f"action={getattr(out, 'action', '?')}")
                    console.print(f"  {label} #{i + 1}: [green]OK[/green] "
                                  f"{time.monotonic() - t0:.1f}s  {detail}")
                except Exception as exc:  # noqa: BLE001 — reported, not raised
                    console.print(f"  {label} #{i + 1}: [red]FAIL[/red] "
                                  f"{time.monotonic() - t0:.1f}s  {type(exc).__name__}: "
                                  f"{str(exc)[:160]}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="bind address"),
    port: int = typer.Option(8000, help="port"),
    reload: bool = typer.Option(False, "--reload", help="reload on source changes"),
) -> None:
    """Serve the encounter viewer: the API and the client, from one process.

    Binds to loopback by default. This is a development viewer with no
    authentication, and starting an encounter spends the account's OpenRouter
    allowance, so exposing it on a public interface would let anyone drain it.
    """
    import uvicorn

    console.print(DISCLAIMER)
    console.print(f"[green]viewer: http://{host}:{port}/[/green]")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        console.print("[yellow]warning: binding beyond loopback. There is no auth, "
                      "and each encounter spends your OpenRouter allowance.[/yellow]")
    uvicorn.run("agentclinic.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
