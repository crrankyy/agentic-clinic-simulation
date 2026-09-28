# 4. Graphs

Built with LangGraph, `StateGraph` constructed explicitly — no prebuilt agent
executors.

## `EncounterState` — `graphs/state.py`

19 keys. Two properties are load-bearing and each was an adversarial review
finding.

### `encounter_log` is a delta channel

```python
encounter_log: Annotated[list[Event], operator.add]
```

Nodes return **only the new events**, never the accumulated list. Returning the
whole list under an `add` reducer duplicates history on every write — and the
duplication compounds, so by turn 10 the transcript is unusable.

### Every key has a declared writer

```python
STATE_SOURCES: dict[str, tuple[str, ...]] = {
    "case_id": ("brief",),
    "turn": ("check_stop",),
    "summary": ("hypothesis",),
    "challenger_opinion": ("challenger", "challenger_final", "orchestrator"),
    "final": ("finalize",),
    ...
}
```

A test fails if any key lacks an entry. You cannot add a channel without
declaring its writer, and declaring it is the moment a reviewer notices that a
new channel carries case content.

The `orchestrator` entries on the opinion keys are not an oversight — it
**clears** them after rendering, which is what makes an opinion one-shot. Action
nodes must **not** clear them.

## `Event`

```python
class Event(BaseModel):
    turn: int
    kind: EventKind          # objective, question, answer, exam, test, literature,
                             # hypothesis, challenge, cost_objection, red_flag,
                             # unlisted_test, parse_failure, budget, stop,
                             # guard, provider_error
    actor: Actor             # doctor, patient, gatekeeper, evidence, system
    text: str
    meta: dict[str, str]
```

The first six kinds are `EVIDENCE_KINDS` — the only ones the hypothesis node
reads (D-058). `guard` is a proposal the repeat guard rejected before it ran;
`provider_error` is a provider still failing after every retry.

**Kind and actor are independent, and that matters for rendering.** The
challenger and cost steward emit with `actor="doctor"` because they are
sub-roles of the doctor side. Styling by actor alone renders them as the doctor
talking to itself — the web client keys off `kind` for exactly this reason.

## Three graphs

### `single_doctor`

```
START → brief → hypothesis → solo_decide → absorb_panel → route_action
          ↑                                                    ↓
          └──────────── route_stop ← check_stop ← action ──────┘
                              ↓
                           finalize → END
```

`solo_decide` is a subgraph containing only the orchestrator. It exists so the
two configurations differ by exactly two nodes, and so the orchestrator sits
behind a schema boundary in **both**.

### `panel` — `graphs/encounter.py` + `graphs/doctor_panel.py`

```
challenge_due? ──yes──> challenger ──> orchestrator ──> order_test? ──yes──> cost_steward
      │                                     │                │                     │
      └──no─────────────────────────────────┘                └──no──> END <────────┘
```

Structurally identical to `single_doctor` except the decision subgraph, plus
`challenger_final` on the voluntary finalize path.

Three properties, each an adversarial review finding:

- **`encounter_log` is absent from both schemas.** That is what stops the
  sub-roles reading raw event text. Opinions cross as **typed keys**.
- **`panel_events` carries `operator.add` *inside* the subgraph** and last-write
  in the parent. Three nodes append in sequence; under last-write the
  orchestrator's write would silently replace the challenger's.
- **The subgraph never returns `encounter_log`.** A compiled subgraph returns its
  final state for declared output keys, **not a delta** — letting a reduced
  channel cross in both directions makes the parent compute `log + (log + new)`
  and double history every turn.

A **cap-forced** finalize does not run the challenger (`D-038`): `finalize` works
from the differential and summary, so an opinion arriving after the budget is
spent could not change any output.

### `interactive` — `graphs/interactive.py`

The same machine with `interrupt()` in place of the orchestrator, so a human
drives the doctor. `cli play` uses it. It exercises the real state machine, not
a simplified one — which is the point.

## Routing — `graphs/routing.py`

`check_stop` is a **node, not a routing function**, because it must *write*
`stop_reason` and increment `turn`. LangGraph routers read state and return an
edge name; they cannot write. An earlier design put cap logic in a router and
therefore had no legal writer for `stop_reason` — every forced stop would have
been reported as a voluntary finalize.

`RoutingError` is raised when a router sees a value it has no edge for. **Never
silently ignored**: a silently dropped action is a doctor whose decision had no
effect, which looks like a model failure and is not.

## Stop conditions

```python
StopReason = Literal["finalize", "turn_cap", "spend_cap", "request_cap",
                     "budget_exhausted", "parse_failure",
                     "provider_error", "no_new_actions"]
```

| Reason | Trigger | Outcome |
|---|---|---|
| `finalize` | the doctor chose to | scored |
| `turn_cap` | `max_turns` reached | scored |
| `no_new_actions` | every proposal in one decision repeated an earlier action (D-056) | scored |
| `spend_cap` | per-case USD cap | error |
| `request_cap` | daily allowance (free tier only) | error |
| `budget_exhausted` | a guard fired mid-node | error |
| `parse_failure` | structured output unrecoverable | error |
| `provider_error` | provider still failing after every retry (D-059) | error |

Everything except `finalize` is a **forced** stop. The runner maps the harness
stops to outcome `error` — they are properties of the harness or the day, not
clinical judgements, and counting them as wrong answers would blame the doctor
for a rate limit. `no_new_actions` is a clinical stop: the output was well-formed
and the doctor simply had nothing new to do.

A stop set *inside* a deliberation (`parse_failure`, `provider_error`,
`no_new_actions`) routes straight to `finalize`. On the panel it used to detour
through `challenger_final`, calling a model for an opinion nothing could act on.

### The last result is read before a turn-cap finalize — M-07

On a turn cap `check_stop` used to go straight to `finalize`, so the final
answer was built from a summary that predated the last result. `route_stop`
now returns `"absorb"` on a cap (not after a budget breach), sending the
encounter through a `hypothesis_final` node first. The interactive graph keeps
the old two-way routing.

> There is deliberately **no confidence-threshold stop rule** (`Q-26`). It would
> truncate exactly the high-confidence cases the calibration analysis needs.

## `budget_guarded` — `graphs/guarding.py`

Every LLM-calling node wears it. **All of them**, not just the likely ones. It
turns three failures into a clean forced finalize instead of an exception:

| Failure | stop_reason |
|---|---|
| `BudgetExceeded` — spend, request or daily cap | `budget_exhausted` / `request_cap` |
| `ProviderUnavailable` — still failing after every retry (D-059) | `provider_error` |
| `StructuredOutputFailed` — repair budget spent | `parse_failure` |

Before D-059 only the first was handled outside the orchestrator: a provider
failure in `hypothesis` or `ask_patient` escaped as an exception and the case
was recorded as a crash — c79bb4e6 died at turn 6 with the answer in hand.
Auth and config failures are deliberately **not** caught: they are run-fatal,
and the runner aborts the evaluation on them rather than crashing each case.

`on_content_failure=False` is for the advisory sub-roles, which degrade to "no
opinion" (D-049) instead of ending the encounter. `channel` is a required
argument: a node inside the decision subgraph cannot write `encounter_log`.

> A Phase 3 review blocker: the decorator once covered **2 of 8** LLM nodes.

## Recursion limits

LangGraph counts **super-steps**, not turns. The limit is derived in code from
the real stop condition so the backstop cannot drift away from it:

```yaml
recursion_limit_multiplier:
  single_doctor: 6
  panel: 10
recursion_limit_headroom: 20
```

**Per graph, because the loop shapes differ** (`D-048`). The panel adds four
super-steps for every finalize the orchestrator proposes and then backs away
from after being challenged. One shared multiplier meant the panel crashed after
about ten re-deliberations — *precisely on the cases where the challenger was
working*, which is the worst possible bias.
