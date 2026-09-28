# Codebase documentation

> **Educational simulation only.** Not medical advice. This project simulates a
> clinical encounter over public benchmark data. It says nothing about clinical
> performance and must never process real patient data.

A doctor agent diagnoses a hidden case by questioning a patient agent, ordering
tests through a gatekeeper agent, and — in the panel configuration — arguing
with a challenger and a cost steward. A judge scores the answer. The doctor
never sees the diagnosis, and that is enforced by how state is shaped rather
than by asking a model nicely.

## Read in this order

| # | Document | What it covers |
|---|---|---|
| 1 | [Architecture](01-architecture.md) | How a turn happens, and the four isolation walls |
| 2 | [Data and isolation](02-data-and-isolation.md) | The dataset, `Case`, the four views, leakage flags, splits |
| 3 | [Agents](03-agents.md) | Patient, gatekeeper, doctor nodes, sub-roles, judge |
| 4 | [Graphs](04-graphs.md) | `EncounterState`, the three graphs, routing, stop conditions |
| 5 | [LLM layer](05-llm-layer.md) | OpenRouter, structured output, guards, tracing |
| 6 | [Evaluation](06-evaluation.md) | Runner, metrics, report, judging separately |
| 7 | [Web app](07-web-app.md) | FastAPI, SSE streaming, replay, the client |
| 8 | [Testing](08-testing.md) | What the 266 tests cover and why each exists |
| 9 | [Operations](09-operations.md) | Running it, configuration, costs, troubleshooting |

## Orientation in one screen

```
                   CaseStore (outside graph state)
                        │
        ┌───────────────┼────────────────┬──────────────┐
        │               │                │              │
   DoctorView     PatientView     GatekeeperView    JudgeView
   objective       Patient_Actor   exams + tests   Correct_Diagnosis
        │               │                │              │
        ▼               ▼                ▼              ▼
   ┌─────────────────────────────────────────┐    ┌──────────┐
   │          the encounter graph            │    │  judge   │
   │  hypothesis → decide → action → stop    │    │ (outside │
   │            (LangGraph)                  │    │  the     │
   └─────────────────────────────────────────┘    │  graph)  │
                        │                          └──────────┘
                        ▼                                ▲
                  FinalAnswer ───────────────────────────┘
```

Ground truth enters exactly one view, which is constructed by the eval runner
**after** the encounter has returned and its state has been discarded.

## The repository

| Path | Contents |
|---|---|
| `server/agentclinic/` | The pipeline: data, agents, graphs, LLM client, eval, CLI, API |
| `server/config/` | Models, budgets, prompts, test synonyms and prices |
| `server/tests/` | The offline suite — no test touches the network |
| `server/scripts/` | Dataset download and verification |
| `client/` | The encounter viewer: static HTML/CSS/JS, no build step |
| `dataset/`, `runs/`, `docs/` | Data, run artefacts, documentation |

Filesystem anchors live in `server/agentclinic/paths.py` and nowhere else.

## A note on how to read this codebase

Most modules open with a docstring explaining *why* the code is shaped as it is,
usually naming the decision (`D-0NN`) or the question (`Q-NN`) behind it. Those
identifiers resolve in [`docs/DECISIONS.md`](../DECISIONS.md), which is the
authoritative record — 62 entries, each with the options considered and the
reason for the choice.

Several of those docstrings describe bugs that actually happened. They are kept
in the deliberate present tense ("returning the whole list duplicates history")
rather than rewritten as history, because they describe traps the next edit
could fall into again.
