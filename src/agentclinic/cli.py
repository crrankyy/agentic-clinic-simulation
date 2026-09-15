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

from .config import load_budgets, load_models, load_test_costs
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
        from .llm.openrouter import LLMCaller, UsageRecorder, build_chat_model

        models = load_models()
        recorder = UsageRecorder()
        model = build_chat_model(
            model=models.for_role("patient"),
            pin_provider=models.provider.pin,
            allow_fallbacks=models.provider.allow_fallbacks,
            attribution_title=models.provider.attribution_title,
        ).with_config(callbacks=[recorder])
        patient = Patient(store.patient_view(case_id), LLMCaller(model, recorder=recorder))

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
