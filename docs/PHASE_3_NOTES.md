# Phase 3 notes — single doctor, judge, eval harness

**Status:** code complete, 163 tests passing offline. The live 3-case run is
pending credentials.

---

## 1. What was built

| Path | Purpose |
|---|---|
| `graphs/schemas.py` | Per-run `Action` type, `HypothesisUpdate`, `FinalAnswer`, `JudgeVerdict` |
| `agents/doctor.py` | `hypothesis`, `orchestrator`, `finalize`, and `budget_guarded` |
| `graphs/single_doctor.py` | The encounter graph + the isolated `solo_decide` subgraph |
| `graphs/nodes.py` | Action nodes shared with the interactive graph |
| `agents/judge.py` | Anthropic-SDK judge, call cap, `top_k` from the judge's own booleans |
| `eval/metrics.py` | Wilson, paired bootstrap, calibration bins, coverage arithmetic |
| `eval/runner.py` | Per-case execution, outcome mapping, crash containment, CSV |
| `eval/report.py` | `report.md` |
| `cli.py run` | The evaluation entry point |
| `config/prompts/{hypothesis,orchestrator,finalize,judge}.md` | Prompts |

## 2. Run commands

```bash
uv run pytest                                          # 163 tests, offline
uv run python -m agentclinic.cli run                   # needs OPENROUTER_API_KEY
uv run python -m agentclinic.cli run --no-report --cache   # dev iteration mode
uv run python -m agentclinic.cli trace <run_id> medqa-0002
```

## 3. Verification

| Check | Result |
|---|---|
| Full encounter reaches a final answer | ✅ |
| Every enabled action routable; disabled action rejected at validation | ✅ |
| Turn cap forces a final answer | ✅ |
| Budget breach finalizes **without another model call** | ✅ |
| Parse failure forces finalize with a distinct `stop_reason` | ✅ |
| Crash becomes a recorded outcome, not an exception | ✅ |
| `encounter_log` never duplicated across the subgraph boundary | ✅ |
| String-scan isolation over leak-free cases (prompts **and** state) | ✅ |
| Orchestrator prompt contains no raw event text | ✅ |
| Hypothesis prompt *does* contain the transcript (the counterpart) | ✅ |
| Judge not called for abstentions; call cap enforced | ✅ |
| Metrics at n=0 and n=1; paired bootstrap on non-overlapping arms | ✅ |
| Report prints `n/a`, never `0.000`, when nothing was scored | ✅ |
| `--cache` refused without `--no-report` | ✅ |

## 4. Surprises

### 4.1 The summary was a transcript in disguise — caught by my own test

`test_orchestrator_prompt_contains_no_raw_event_text` failed, and it was right
to. The `hypothesis` node built `summary.findings` as `f"{e.actor}: {e.text}"`
straight from `encounter_log`. So although the decision subgraph's schema
genuinely excludes `encounter_log` — the structural isolation worked — the
orchestrator was reading verbatim event text *through the summary*.

That defeats Q-29 entirely, and it is exactly the failure mode round 2's finding
B-7 was about: for the 29 cases where the dataset embeds the diagnosis in test
results, it is the difference between the doctor seeing it once and seeing it on
every subsequent turn.

Fixed by making `findings` a field the **model** writes, in its own words, with
the prompt stating that later turns see the summary rather than the exchange.
`tests_ordered` stays mechanical — those are the doctor's own requests, and
contain no result text.

The lesson generalises: a structural boundary is only as good as what you pour
through it.

### 4.2 A state key that silently did not cross the subgraph boundary

`test_parse_failure_forces_finalize_with_a_distinct_stop_reason` asserted
`stop_reason` and `parse_failures`. The first passed, the second did not:
`parse_failures` was missing from `DecideOutput`, so the orchestrator's write
was discarded at the boundary. Nothing errored — the value was simply dropped.

Worth noting that `spend_usd` is deliberately **not** in `DecideOutput` either,
but for the opposite reason: D-039 makes the client the authoritative total and
`check_stop` its only writer, so admitting it would give the key two writers and
break the property the allow-list test relies on. The plan's §2.3 schema list
was written before D-039 and still names it; the code is correct and the
discrepancy is recorded here.

### 4.3 A forced finalize must not call the model, for two different reasons

The budget path was already specified that way. The parse-failure path was not,
and the first implementation called the model — which promptly exhausted the
test script. It should not call: the model has just failed to produce valid
output three times, so a fourth attempt is the least likely thing to work, and
under a budget breach the call is precisely what is unavailable.

## 5. Decisions applied

- **D-026** Test-selection merged → `OrchestratorDecision.expected_information`
- **Q-14** differential capped at 8 → `MAX_DIFFERENTIAL`
- **Q-15** 3 attempts, then a distinct `parse_failure` stop → `LLMCaller.structured`
- **Q-17** `red_flag` as a list → `FinalAnswer`
- **Q-21** judge outside the graph → `eval/runner.py` builds `JudgeView` after the encounter
- **Q-22** correctness derived from `match_type` → `JudgeVerdict.correct`
- **Q-23** top-k from the judge's per-entry booleans → `top_k`
- **D-021 / D-037** Anthropic SDK judge, 200-call cap → `agents/judge.py`
- **D-028** abstention/error/crash arithmetic → `eval/metrics.py`
- **D-039** client owns the spend total → `LLMCaller`, `check_stop`
- **C-4** budget short-circuits by edge → `route_action` priority 1
- **Q-34** cache hard-disabled for reported runs → `cli.run` assertion

## 6. Pending

The **live spike** still needs `OPENROUTER_API_KEY`, and the judge needs
`ant auth login`. Until then: strict structured output on the free model is
unverified, `usage.cost` behaviour for `:free` models is unknown, and no live
3-case run has happened. Everything above is offline verification.
