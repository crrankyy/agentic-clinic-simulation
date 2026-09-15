# 1. Architecture

## The problem the design exists to solve

A doctor agent must diagnose a case it cannot see. The obvious implementation —
put the case in shared state, tell each agent what it may look at — fails for a
reason that has nothing to do with model quality: **a prompt instruction is not
an access control.** A summarisation step that copies text, a checkpoint that
records config, an exception message that quotes a prompt, a new state key added
without thought — each defeats it silently, and none shows up as a test failure.

So the guarantee here is deliberately narrow and structural:

> No `EncounterState` key is ever written from `Correct_Diagnosis` or
> `Management_and_Follow_Up`, because no doctor-side view exposes them.

Note what that does **not** claim. The diagnosis *does* appear in the transcript
for 29 of the 214 cases, because the benchmark embeds it in the test results the
gatekeeper is designed to return. That is a property of the dataset, is measured
rather than hidden (`dx_in_results`), and cannot be fixed by isolation.

## One turn, end to end

```
  brief ──> hypothesis ──> [ decide subgraph ] ──> absorb_panel ──> route_action
              ▲              orchestrator picks                          │
              │              one action                      ┌───────────┴─────────┐
              │                                              ▼           ▼         ▼
              │                                        ask_patient  order_test  finalize
              │                                              │           │         │
              │                                              └─────┬─────┘         │
              │                                                    ▼               │
              └──────────────── route_stop <──────────────── check_stop            │
                                     │                                             │
                                     └────────────> finalize ──> END <─────────────┘
```

1. **`brief`** seeds the objective. It is the doctor's referral line and the only
   case content the doctor starts with.
2. **`hypothesis`** is the *only* node that reads `encounter_log`. It maintains
   the differential and writes a summary **in its own words**.
3. **The decision subgraph** receives that summary and the differential — never
   the log. Its `input_schema` omits `encounter_log` entirely, so the exclusion
   is enforced by LangGraph, not by the prompt.
4. **An action node** executes one action and appends events.
5. **`check_stop`** increments the turn and decides whether to continue.

## The four walls

Each wall is a different mechanism, because a single mechanism has a single
failure mode.

### Wall 1 — the case never enters graph state

`CaseStore` holds `Case` objects outside LangGraph and **exposes no method that
returns one**. The only exits are four frozen views with disjoint slices. A test
asserts by reflection that no such method exists, so adding one fails the suite.

Views are bound into nodes **by closure** at graph-construction time, never
passed via `config["configurable"]` — LangGraph records config in checkpoint
metadata, which would put ground truth in a checkpoint.

### Wall 2 — the subgraph boundary

The orchestrator, challenger and cost steward run inside a subgraph whose
`input_schema` and `output_schema` omit `encounter_log`. They cannot read raw
event text because the channel is not in their schema. Opinions travel back as
**typed keys**, not as prose appended to a log.

### Wall 3 — the summary is model-authored

`summary.findings` is written by the hypothesis model in its own words, never
copied from events (**D-041**). This one was found by a test rather than by
review: the schema boundary held perfectly while the orchestrator read the
transcript *through* the summary, because an earlier version copied event text
into it. For the 29 flagged cases that meant the doctor saw the diagnosis on
every subsequent turn instead of once.

The counterpart is that `summary.tests_ordered` **is** mechanical — those are
the doctor's own requests and carry no result text. As of **D-055** each request
also carries whether it returned anything, which states that the case holds
nothing under that request, never what it does hold.

### Wall 4 — the judge is outside the graph

The judge is the only component that sees `Correct_Diagnosis`. It is constructed
by the eval runner **after** the encounter returns and its state is discarded
(**Q-21**). Its records go to `judge.jsonl`, never to a per-case trace, and a
judge exception is recorded by **type only** — an exception message can carry
its prompt, and the judge's prompt contains the answer.

## `STATE_SOURCES` — every key has a declared writer

```python
STATE_SOURCES = {
    "case_id":      ("brief",),
    "turn":         ("check_stop",),
    "summary":      ("hypothesis",),
    "final":        ("finalize",),
    ...
}
```

A test fails if any `EncounterState` key lacks an entry. This is how an
undeclared channel for ground truth gets caught: you cannot add a key without
saying which node writes it, and saying so is exactly the moment a reviewer
notices.

It also documents non-obvious ownership. `challenger_opinion` lists
`orchestrator` as a writer because the orchestrator **clears** it after
rendering — which is what makes an opinion one-shot rather than permanent.

## Two configurations

Both graphs are deliberately identical except for the decision subgraph, so a
comparison between them means *"does a doctor receiving challenge and cost
opinions decide differently"* rather than confounding sub-roles with loop shape.

| | `single_doctor` | `panel` |
|---|---|---|
| Decision subgraph | orchestrator alone | challenger → orchestrator → cost steward |
| Challenger | — | every 3rd turn, and once before a voluntary finalize |
| Cost steward | — | reviews test orders only |
| Calls per case (measured) | ~41 | ~77 |
| Recursion multiplier | 6 | 10 |

The sub-roles are **advisory**: `ChallengerOpinion` has no `should_reopen` field
and `CostStewardOpinion` has no `replacement_action`. Neither can force a
decision; the orchestrator reads their opinions and chooses (**D-025**).

## Where to look for what

| Question | File |
|---|---|
| What can each agent see? | `data/views.py` |
| What flows through state? | `graphs/state.py` |
| How does one turn execute? | `graphs/single_doctor.py`, `graphs/nodes.py` |
| How does the panel differ? | `graphs/doctor_panel.py`, `graphs/encounter.py` |
| How is a test request matched? | `agents/gatekeeper.py` |
| How is a run scored? | `eval/runner.py`, `eval/metrics.py` |
| How does the web viewer stream? | `api/engine.py`, `api/wire.py` |
