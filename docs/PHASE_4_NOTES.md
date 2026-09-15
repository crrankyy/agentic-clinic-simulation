# Phase 4 notes — the doctor panel

**Status:** code complete, 204 tests passing offline, one live 3-case run
(`dev-panel-d14c436b`) executed and compared against the single-doctor arm.

---

## 1. What was built

| Path | Purpose |
|---|---|
| `graphs/doctor_panel.py` | The `decide` subgraph: hypothesis → orchestrator, with the challenger and cost-steward as advisory side-roles |
| `graphs/encounter.py` | The panel encounter graph, including `challenger_final` on the finalize path |
| `agents/doctor.py` | `make_challenger_node`, `make_cost_steward_node`, `advisory()` |
| `graphs/schemas.py` | `ChallengerOpinion`, `CostStewardOpinion` |
| `config/prompts/{challenger,cost_steward}.md` | Sub-role prompts |
| `eval/runner.py` | `rebuild_results()` — recovering a run whose report generation crashed |

## 2. Run commands

```bash
uv run pytest                                              # 204 tests, offline
uv run python -m agentclinic.cli run --config panel        # needs OPENROUTER_API_KEY
uv run python -m agentclinic.cli judge <run_id> --verdicts <path>   # re-judge / re-report
uv run python -m agentclinic.cli compare <run_a> <run_b>
```

## 3. Verification

| Check | Result |
|---|---|
| The subgraph cannot see `encounter_log` | ✅ |
| Both opinion channels cross the boundary in **and** out | ✅ |
| Challenger schedule fires on turn 3, 6, 9 and once before finalize | ✅ |
| The finalize re-decision happens exactly once, and only on new information | ✅ |
| An executed action clears the finalize flag | ✅ |
| Three panel turns leave the log unduplicated and complete | ✅ |
| A cost objection reaches the next orchestrator decision, and is one-shot | ✅ |
| A cost objection does **not** block the order (advisory, D-025) | ✅ |
| A cap-forced finalize skips the challenger | ✅ |
| The panel orchestrator never sees raw event text | ✅ |
| The challenger never sees raw event text | ✅ |
| Ground truth reaches no panel prompt, for leak-free cases | ✅ |
| An advisory node that cannot produce output does not kill the case | ✅ |
| A dangerous alternative reaches the orchestrator **with its likelihood** (D-051) | ✅ |
| A rebuilt run does not report unrecorded telemetry as zero | ✅ |

## 4. Surprises

### 4.1 The panel was one advisory sub-role, not two — and the tests agreed

`cost_objection` was declared in `PanelOutput` but **not** in `PanelInput`, and
`_CLEARS` wiped it. So the cost steward's objection was produced, written,
carried out of the subgraph, cleared, and never read. Half of D-025 — "its
objection informs subsequent decisions" — silently did not happen.

The tests were green because they covered the half that worked: they asserted
the objection did not *block* the order (true, and by design), never that it
*reached* the next decision. The defect was inherited from PLAN.md §2.3, whose
`input_schema` list carried the same omission through three adversarial review
rounds. A schema read three times by reviewers looking for exactly this class of
bug still shipped it, because everyone read it as a description rather than as
the thing that actually gates the data.

### 4.2 The recursion limit was never re-derived for the new graph

`recursion_limit_multiplier` was tuned for `single_doctor`. The panel adds a
hypothesis call, up to two advisory calls, and a re-deliberation per challenge —
so the panel crashes at roughly ten re-deliberations. That is precisely the
regime where the challenger is *working*: the better the sub-role does its job,
the sooner the graph dies. D-048 makes the limit per-graph.

### 4.3 A crash in the cheap half nearly cost the expensive half

The run completed all 232 encounters, then died writing the report on
`NameError: name 'manual' is not defined` — a `judge`-only variable referenced
in `run`, from an edit applied to both commands. `results.csv` was never
written. `finals.json` survived, so `rebuild_results()` (D-050) recovered the
run without re-spending 232 requests or 27 minutes.

### 4.4 …and the recovery then overclaimed what it had recovered

Following up on 4.3: encounter events are **not** persisted anywhere. The
rebuild derives its counters from per-node LLM calls, which means `turns` and
`tests_ordered` are lower bounds — the gatekeeper only calls the model when its
cheap match tiers miss (D-045) — and exams, unlisted requests, match tiers, red
flags and simulated cost are not recoverable at all. Those fields sat at their
dataclass defaults and the report printed them as measurements: `exams: 0`,
`unlisted requests: 0`, `gatekeeper match tiers: (none)` — for a run that
ordered 25 tests. `behaviour_recovered=False` now marks such rows and the report
prints `n/a (not recorded)`, matching the `n/a`-not-`0.000` discipline the
accuracy section already had. `rebuild_results`' own docstring had asserted the
counts were "the same numbers the live path would have produced"; it was wrong,
and it is corrected.

**Still open:** the durable fix is to persist encounter events to the trace so a
future crash loses nothing. Not done — the tracer has no method for it, and
adding one is a change to the Phase 3 observability surface that should be
decided rather than slipped in here.

## 5. The live run

`dev-panel-d14c436b`: 3 cases, 232 LLM calls, 27 minutes, 8.5 req/min against an
18/min bucket — the model, not the limiter, is the ceiling.

| | single_doctor | panel |
|---|---|---|
| strict accuracy | 1.000 (3/3) CI [0.439, 1.000] | 0.667 (2/3) CI [0.208, 0.939] |
| top-1 / top-3 | 3/3 · 3/3 | 2/3 · 3/3 |
| mean confidence | 0.84 | 0.43 |
| turns | 37 | 50 |

`panel − single_doctor: −0.333, 95% CI [−1.000, 0.000]` over 3 paired cases.
**This is not evidence of anything.** One case flipping moves the point estimate
by 33 points.

What *is* worth recording is the mechanism, because it is traceable. Both arms
score top-3 = 3/3, so the panel did not lose `medqa-0012` — it **demoted** it.
The correct answer sat at rank 2 (p=0.30); the orchestrator ranked
"Metabolic/mitochondrial disorder" first, stating *"ranked highest because it is
the most dangerous diagnosis if missed"* — the challenger prompt's own language.
A rank inversion produced by the sub-role, not by the evidence. The confidence
collapse across all three cases (0.84 → 0.43, including the two it got right) is
the same effect expressed as calibration.

D-051 addresses it structurally: severity and probability are now two fields
rather than one sentence. Whether that helps is a Phase 6 question on a real
split — the claim in D-051 is only that the old schema could not express the
distinction its own prompt depended on.

## 6. Decisions applied

D-048 (per-graph recursion limit), D-049 (advisory degradation, one-shot
opinions), D-050 (recoverable runs), D-051 (severity ≠ probability).

## 7. Pending

- Persisting encounter events to the trace (see 4.4).
- Phase 5: evidence agent (PubMed + openFDA) and judge calibration. Q-18 needs
  `NCBI_EMAIL`; the evidence agent is skipped entirely if unset.
- The judge's eventual billing route (R8) stays open — nothing needs deciding
  until evaluation scales past 3 cases.
