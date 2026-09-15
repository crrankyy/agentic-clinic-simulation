# Phase 3 review — the diff

2026-09-15 — fresh-context review of `7f541cf..working tree`
(5 tracked files modified, 13 new files: doctor nodes, schemas, `single_doctor`,
judge, runner, metrics, report, repair loop, 4 prompts, 3 test files).

Scope as briefed: information leakage, hidden assumptions, correctness of the
diff. `uv run pytest` → **163 passed**. All claims below were verified by
reading the code and by read-only probes against the compiled graph; no project
file other than this one was modified.

**The headline first: the isolation property holds.** I tried to break it and
could not. `Correct_Diagnosis` / `Management_and_Follow_Up` are reachable only
through `JudgeView`, which is constructed in `runner.run_case` *after*
`graph.ainvoke` returns (`runner.py:158`), and `judge.build_prompt` is its only
consumer. The decision subgraph really is sealed: I embedded `build_solo_decide`
in a parent graph, wrote a gatekeeper event into the parent's `encounter_log`,
and the orchestrator saw `encounter_log == []`. The key name exists inside the
subgraph (LangGraph creates every channel of the internal `EncounterState`
schema) but it is **never seeded** from the parent, so the structural claim in
`single_doctor.py:9-13` is true in substance. `summary.findings` is
model-authored (`schemas.py:56`, `doctor.py:113-118`), `summary.tests_ordered`
copies only the doctor's own request text, no checkpointer is attached in batch
runs, and `tracer.llm_call` records no prompt or completion text. The one place
ground truth can still escape containment is finding 2, and it is an
error-handling omission, not a data-flow one.

## Findings

### 1. `@budget_guarded` covers 2 of the 8 nodes the plan requires; a budget breach inside `ask_patient` or `finalize` crashes the case
- **Severity:** blocker
- **File:** `src/agentclinic/agents/doctor.py:147,196,248`; `src/agentclinic/graphs/nodes.py:25,42,61`
- **Problem:** PLAN §5.5 says the decorator wraps **every** LLM-calling node —
  "`hypothesis`, `orchestrator`, `challenger`, `challenger_final`,
  `cost_steward`, `ask_patient`, `finalize`, and the gatekeeper's LLM tier".
  `grep budget_guarded` returns exactly two call sites: `hypothesis` and
  `orchestrator`. `make_finalize_node` returns the bare `finalize`
  (`doctor.py:248`) and `make_ask_patient` the bare node. The decorator's own
  docstring names the hole it leaves open — *"including `finalize` itself, after
  which there is no recovery node"* — and then `finalize` is not wrapped.
  Verified against the compiled graph with a `SpendTracker` that raises on the
  n-th call:

  | breach first seen in | result |
  |---|---|
  | `hypothesis` | OK, `stop_reason=budget_exhausted`, final answer produced |
  | `orchestrator` | OK, `stop_reason=budget_exhausted`, final answer produced |
  | **`ask_patient`** (model-backed patient) | **`BudgetExceeded` escapes the graph** |
  | **`finalize`** | **`BudgetExceeded` escapes the graph** |

  `finalize`'s internal `if forced:` branch only covers a breach that happened
  *earlier*; a breach that first raises in `finalize`'s own `_before` is
  unprotected. The realistic trigger is not the spend cap (free-tier `usage.cost`
  is `None`, so `SpendTracker` never accumulates) but `DailyCapExceeded`, which
  `guards.daily.check()` raises on **every** subsequent call once the 1000/day
  allowance is gone. The runner then records outcome `crash` with no final
  answer — silently shrinking the accuracy denominator, which is precisely the
  behaviour PLAN §5.1 says must not happen.
- **Fix:** wrap `finalize` in `budget_guarded` and have the wrapper fall through
  to the Python-assembled answer rather than returning `{}` (a `finalize` that
  returns `{}` leaves `final is None` → outcome `crash` again, so the decorator
  needs a `finalize`-specific fallback, e.g. wrap only the model call in
  `try/except BudgetExceeded` inside `finalize` and reuse the `forced` branch).
  Wrap `make_ask_patient` (and `make_search_literature`, and the gatekeeper node
  once the LLM tier is wired — finding 3) with `budget_guarded(..., "encounter_log")`.
  Add the two tests named in finding 9.

### 2. A judge failure destroys the whole run: no results, no report, and the only path by which ground truth can leave containment
- **Severity:** blocker
- **File:** `src/agentclinic/eval/runner.py:114-169`, `190`; `src/agentclinic/agents/judge.py:62-88`
- **Problem:** `run_case`'s `try/except` closes at line 127, around
  `graph.ainvoke` only. The judge call at line 157 is **outside** it, and
  `run_evaluation` uses `asyncio.gather` with no `return_exceptions`. Verified:
  a judge that raises makes `run_evaluation` raise, `asyncio.run(...)` in
  `cli.py:278` propagates, and the CLI never reaches `write_report` /
  `write_results_csv` — every completed encounter is discarded along with the
  free-tier requests it cost. The runner's module docstring says *"Never raises
  — a crash becomes a result"*; that is false. Live triggers, in order of
  likelihood: `AsyncAnthropic()` in `_ensure_client` raising because
  `ant auth login` has not been run (still an open user action in PLAN §8);
  any `APIStatusError` / overload / timeout; `parsed.parsed_output` returning
  `None` (it is an `Optional` property on `ParsedMessage` in `anthropic 1.5.0`,
  and the code assigns it straight to a `JudgeVerdict` with no check, so a
  refusal or a `max_tokens` truncation becomes `AttributeError` on line 160);
  `JudgeCallCapExceeded`.
  This is also the leakage finding. PLAN §3.3 item 8 requires judge exceptions
  to go to **`judge.jsonl` only**, precisely because an Anthropic SDK error can
  echo the request that carried `Correct_Diagnosis` (review B-17). The code
  records them **nowhere**: the exception escapes to a terminal traceback whose
  message is outside any of the containment the plan specifies. Conversely, the
  naive fix — moving the judge call inside the existing `except` — would write
  that same message into `result.error` and therefore into `results.csv`, a
  doctor-side artefact. Both directions are currently wrong.
- **Fix:** wrap the judge call in its own `try/except`; on failure set
  `outcome="error"` with a **fixed, message-free** marker in `result.error`
  (e.g. `"judge_failed"`), and pass the exception to `tracer.judge(...)` /
  `judge.jsonl` alone. Treat `parsed_output is None` as a judge failure rather
  than dereferencing it. Give `run_evaluation` `return_exceptions=True` (or an
  inner guard) so one bad case cannot cancel the batch, and have the CLI write
  `results.csv` before it can fail for any reason.

### 3. The gatekeeper's LLM tier is never wired, so every non-exact request becomes a fabricated "not available"
- **Severity:** major
- **File:** `src/agentclinic/cli.py:273` (and `cli.py:138`)
- **Problem:** `Gatekeeper(store.gatekeeper_view(case_id), costs)` is constructed
  with no `llm_disambiguate`. `grep` confirms the argument is supplied nowhere
  outside `tests/test_gatekeeper.py`, and `config/prompts/gatekeeper_disambiguate.md`
  is loaded by nothing. PHASE_2_NOTES §4.2 records this explicitly as deferred
  work — *"Phase 3 wires the real caller in"* — and Phase 3 did not. Consequence
  in a real run: tier 3 (D-034's LLM fallback over key names) and D-035's
  ambiguous-leaf disambiguation both return `None`, so `match` falls through to
  `unmatched`; the doctor is told "Not available for this patient", is charged
  the median price, and an `unlisted_test` event is logged. That is the exact
  failure `gatekeeper.py:6-8` warns about — *"every failure becomes a fabricated
  unavailability that changes what the doctor believes"* — and it inflates the
  `unlisted_tests` metric while making `llm` / `llm_disambiguated` structurally
  absent from `match_tiers` in every report.
- **Fix:** build the disambiguator in `cli.run` from the existing `caller` and
  `config/prompts/gatekeeper_disambiguate.md` (an async `(request, candidates) ->
  key | None` over key names only), pass it into `Gatekeeper`, and route it
  through `budget_guarded` per finding 1.

### 4. No pre-flight refusal, and a daily-cap breach degrades case-by-case instead of aborting the run
- **Severity:** major
- **File:** `src/agentclinic/cli.py:246-247`
- **Problem:** PLAN §5.1 specifies a pre-flight that *"refuses runs projected
  over spend or remaining daily requests"*, and §5.1's closing paragraph
  requires a daily-cap breach to *"abort the run cleanly with partial results
  written, rather than degrading case by case into `error` outcomes that
  silently shrink the denominator"*. The code computes `remaining =
  guards.daily.remaining()`, prints it, and never reads it again — the variable
  is otherwise dead. There is no projection check and no run-level abort:
  `DailyCapExceeded` subclasses `BudgetExceeded`, so wherever it is caught it
  produces exactly the per-case `error` degradation the plan rules out (and
  where it is not caught, finding 1's crash).
- **Fix:** before building the graphs, refuse the run when
  `remaining < len(selected) * expected_calls_per_case` (calls per case are
  bounded above by `3 * max_turns + 1` for `single_doctor`: hypothesis +
  orchestrator each turn, at most one patient or gatekeeper-LLM call per action,
  plus `finalize`). Catch `DailyCapExceeded`
  at the `run_evaluation` level, stop scheduling new cases, and write the
  partial `results.csv` / `report.md` with the shortfall stated in the header.

### 5. `api_cost_usd` is read from a stale snapshot, and one `UsageRecorder` is shared by four concurrent cases
- **Severity:** major
- **File:** `src/agentclinic/eval/runner.py:135`; `src/agentclinic/cli.py:251,265,280`
- **Problem:** two independent defects in the same number.
  (a) `result.api_cost_usd = float(state.get("spend_usd", 0.0))` reads the
  snapshot that only `check_stop` writes (`routing.py:36`). `check_stop` runs
  after an *action*, so the snapshot always excludes the final
  `hypothesis` + `orchestrator` + `finalize` calls, and for an encounter that
  finalizes on turn 1 `check_stop` never runs at all — `api_cost_usd` is then
  `0.0` for three real calls. The authoritative total D-039 talks about is
  `guards.spend.case_total(case_id)`, which the runner has no reference to.
  (b) `cli.run` creates **one** `UsageRecorder` and one `LLMCaller`, shares them
  across `concurrency: 4` (`budgets.yaml`), and `UsageRecorder.last` is a single
  slot. `LLMCaller._after` reads `recorder.last` after awaiting `ainvoke`, so
  under concurrency the usage attributed to a call — cost added to
  `SpendTracker` *and* tokens written to the trace — can belong to another
  case's call. The same object holds `LLMCaller.parse_failures`, likewise
  cross-case.
  Both are latent today only because free endpoints return no `cost`, which is
  also why they would go unnoticed until the first paid run — the moment the
  per-case spend cap starts to matter.
- **Fix:** pass the `SpendTracker` (or the `LLMCaller`) into `run_case` and read
  `spend.case_total(case.case_id)` for `api_cost_usd`; keep `state["spend_usd"]`
  as the reported snapshot it is declared to be. Give each case its own
  `UsageRecorder` + `LLMCaller` (build them inside `build_graph`, which is
  already per-case), sharing only `RunGuards`.

### 6. `StopReason` drift is undocumented: `parse_failure` added, `stop_reason` now crosses the subgraph, `spend_cap` is unreachable
- **Severity:** major
- **File:** `src/agentclinic/graphs/state.py:35-36,127`; `src/agentclinic/graphs/single_doctor.py:58`; `src/agentclinic/graphs/routing.py:36-51`
- **Problem:** three choices with no trace to PLAN or DECISIONS, which the brief
  makes blockers on documentation grounds even where behaviour is sound:
  1. `"parse_failure"` is added to the `StopReason` literal. Q-15 specifies a
     *"distinct `parse_failure` **outcome column**"*, not a stop reason, and
     PLAN §4.1's literal does not contain it.
  2. `stop_reason` is in `DecideOutput` and gains `"orchestrator"` in
     `STATE_SOURCES`, directly contradicting PLAN §2.3: *"`case_id`,
     `test_cost_usd`, `stop_reason` and `final` never cross either way."*
  3. `"spend_cap"` is now **dead**. `check_stop` takes `spend_total` but only
     stores it as a snapshot; it never compares it to a cap, so PLAN §5's row
     *"spend_cap | $0.50 | written by `check_stop`"* describes code that does not
     exist. The only other producer rewrites it: `doctor.py:92` maps
     `exc.kind == "spend_cap"` to `"budget_exhausted"`. A value that can never be
     produced is also absent from `runner.HARNESS_STOPS`, so if it ever were
     produced the case would be classified `scored` — a harness stop scored as a
     clinical one.
  I traced every path for (1) and (2) and found no live bug: on a parse failure
  the orchestrator also sets `action="finalize"`, `route_action` sends it to
  `finalize`, and `finalize` leaves the existing `stop_reason` alone, so the
  three writers never contend. The objection is that none of it is recorded.
- **Fix:** add a decision entry (or a PLAN §10 deviation row) for the
  `parse_failure` stop reason and for `stop_reason` crossing the decision
  boundary, and correct PLAN §2.3's "never cross" sentence. Either implement the
  `spend_cap` comparison in `check_stop` or delete `"spend_cap"` from
  `StopReason` and say in PLAN §5 that the client-side guard subsumes it.

### 7. `HypothesisUpdate.findings` is a new schema field, and `EncounterSummary`'s docstring now describes the opposite of what the code does
- **Severity:** minor
- **File:** `src/agentclinic/graphs/schemas.py:56`; `src/agentclinic/graphs/state.py:59-62`; `src/agentclinic/agents/doctor.py:113-118`
- **Problem:** PLAN §4.1's `HypothesisUpdate` has four fields; the code adds a
  fifth, `findings`, and PLAN §4.1 annotates `EncounterSummary.findings` as
  *"hypothesis, **from encounter_log**"*. The implementation instead takes it
  from the model's own words — which is the **right** call, and is the thing that
  makes the "the summary is not a transcript" claim true, but it is an unrecorded
  design change. `state.py:59` still carries the old story: *"`findings` and
  `tests_ordered` are derived from `encounter_log` by the hypothesis node"*. A
  future reader who trusts that docstring and "fixes" `doctor.py` to match it
  would reopen the transcript path into the orchestrator's prompt for all 29
  flagged cases.
- **Fix:** record the change (it is a real improvement), correct PLAN §4.1's
  annotation, and rewrite the `EncounterSummary` docstring to say `findings` is
  model-authored and only `tests_ordered` is mechanical.

### 8. Parse-failure accounting under-reports; the counter that sees repairs is read by nothing
- **Severity:** minor
- **File:** `src/agentclinic/llm/openrouter.py:142,187,199`; `src/agentclinic/agents/doctor.py:190`
- **Problem:** answering the brief's question directly — the repair loop does
  **not** double-count and does **not** mis-charge (`_after` is called on the
  failed attempt too, so a failed call's cost and latency are recorded), and it
  correctly retries only `ValidationError`/`ValueError`, leaving transport
  retries to `ChatOpenAI(max_retries=...)`. What it does is under-count: a call
  that succeeds on attempt 2 adds 1 to `LLMCaller.parse_failures` and **nothing**
  to the `parse_failures` state channel, because only the orchestrator writes
  that channel and only on total failure. `STATE_SOURCES` declares eight writers
  for the key; one exists. So `report.md`'s "parse failures: N" reads 0 for a run
  in which every node needed a repair — the exact signal Q-15 wants visible.
  `LLMCaller.parse_failures` itself is cumulative across concurrent cases (see
  finding 5b) and is read by no code.
- **Fix:** have `structured` return the attempt count (or accept a per-call
  sink) so each node can return `{"parse_failures": attempts - 1}` as a delta;
  drop the unread instance counter.

### 9. Test gaps that let findings 1 and 2 through, plus one assertion that cannot fail
- **Severity:** minor
- **File:** `tests/test_runner.py:79`, `tests/test_single_doctor.py:72-86`
- **Problem:** `assert "Traceback" not in (result.error or "")` is trivially
  true: `result.error` is built as `f"{type(exc).__name__}: {exc}"`, which cannot
  contain a traceback unless the message does. It documents intent but guards
  nothing. More consequentially, the suite's budget test breaches the cap at the
  *first* call, which is `hypothesis` — the one LLM node that is guarded — so it
  passes while `ask_patient` and `finalize` are unprotected; and nothing
  exercises a judge that raises, so the run-killing path in finding 2 is
  invisible. Phase 3's acceptance criterion also lists the recursion limit as
  tested; no test covers it end to end (I verified separately that a 20-turn
  encounter takes 102 supersteps against the derived limit of 140 — the headroom
  is correct, it is simply unasserted).
- **Fix:** add (a) a budget test whose breach lands on `ask_patient` and one
  that lands on `finalize`, asserting a final answer still exists; (b) a runner
  test with a judge that raises, asserting the other cases still return results;
  (c) a `max_turns=20` graph run under `recursion_limit=load_budgets().recursion_limit`.
  Replace the `"Traceback"` assertion with one that checks `result.error` equals
  the type-and-message form.

### 10. `budget_guarded`'s default channel is a trap for the Phase 4 sub-role nodes
- **Severity:** minor
- **File:** `src/agentclinic/agents/doctor.py:69`
- **Problem:** the default is `channel="encounter_log"`, but any node inside the
  decision subgraph that writes `encounter_log` writes to the subgraph's own
  channel, which is absent from `DecideOutput` and is therefore **silently
  dropped** at the boundary — no error, just a lost budget event. The
  `orchestrator` call site passes `"panel_events"` explicitly and the docstring
  explains why, so today it is correct; the next sub-role node wrapped without
  the second argument will lose its event.
- **Fix:** make `channel` a required argument, or default it to `"panel_events"`
  and pass `"encounter_log"` explicitly at the two parent-graph call sites.

## Verdict

**NO-GO** on the diff as it stands — but narrowly, and not on the axis the brief
was most worried about.

The information-leakage design is sound and the code implements it: ground truth
reaches exactly one function, outside the graph, after the encounter is over; the
decision subgraph genuinely cannot see `encounter_log`; the summary is
model-authored; the `dx_in_results` distinction is handled correctly everywhere I
looked (flag carried onto `CaseResult`, dual accuracy blocks with denominators,
the 29 cases treated as measured data rather than suppressed). The metrics
arithmetic is right at n=0 and n=1 — `accuracy` returns `None` rather than `0.0`,
`coverage` excludes errors and crashes from both terms, Wilson is guarded at
`n == 0`, empty calibration bins are emitted, the paired bootstrap is restricted
to cases scored in both arms. The outcome mapping is correct for every situation
I could construct except the two crash paths in findings 1 and 2. The repair loop
neither double-counts nor mis-charges. Routing has an edge for every value the
per-run `Literal` can produce and raises otherwise, and the derived recursion
limit has real headroom.

What must change before this is called done:

1. **Finding 1** — wrap `finalize` and `ask_patient` (and the gatekeeper node
   once finding 3 lands). A budget or daily-cap breach must never turn a case
   into `crash`.
2. **Finding 2** — contain judge failures inside `run_case`, treat
   `parsed_output is None` as a failure, keep the exception message in
   `judge.jsonl` and out of `results.csv`, and make `run_evaluation` survive one
   bad case. Today a missing `ant auth login` burns every OpenRouter request the
   run cost and writes nothing.
3. **Finding 3** — wire the gatekeeper's LLM tier, or state in the report that
   tiers 3 and `llm_disambiguated` are inactive, because until then every
   `unlisted_test` count and `match_tiers` breakdown describes a cascade running
   at two thirds strength.
4. **Findings 4, 5, 6** — the pre-flight and the run-level daily abort, per-case
   cost attribution, and a decision entry for the `StopReason` / subgraph-boundary
   drift.

Findings 7–10 can ride along with the above. None of them changes a number.

---

# Resolutions

Appended 2026-09-15. **All 10 findings accepted.**

| # | Severity | Resolution |
|---|---|---|
| 1 | blocker | `budget_guarded` moved to `graphs/guarding.py` and applied to **every** LLM-calling node — `hypothesis`, `orchestrator`, `ask_patient`, both gatekeeper nodes, `search_literature`, `finalize`. `channel` is now a **required** keyword (also fixes #10). `finalize` uses `skip_if_exhausted=False` and additionally catches `BudgetExceeded` itself, because the decorator would return a budget update with no `final` and nothing runs after finalize. Tests added for a breach landing on `ask_patient` and on `finalize`. |
| 2 | blocker | The judge call is wrapped in its own `try/except` inside `run_case`: a failure sets outcome `error` and records **only the exception type** in `results.csv`, with the message going to `judge.jsonl`. `parsed_output` is checked for `None` and raises `JudgeParseFailure` rather than being dereferenced. `gather` uses `return_exceptions=True`. Three tests added, including one asserting ground truth never reaches `results.csv`. |
| 3 | major | `make_llm_disambiguator` added and wired in `cli.run`, so the cascade's tier 3 and ambiguous-leaf resolution actually function in a live run. It is shown key names only. |
| 4 | major | Pre-flight refusal added: the run is rejected when projected requests exceed the remaining daily allowance, with the shortfall printed. |
| 5 | major | `api_cost_usd` now reads `SpendTracker.case_total(case_id)` — the authority under D-039 — and `LLMCaller` counts parse failures **per case** rather than cumulatively, so a shared caller cannot attribute one case's repairs to another. |
| 6 | major | Recorded as **D-040**; PLAN.md §4.1 and §10 corrected. |
| 7 | minor | Recorded as **D-041**; PLAN.md §4.1 and the `EncounterSummary` docstring corrected. |
| 8 | minor | `LLMCaller.parse_failures_for(case_id)` is now read by `run_case`, which takes the max of the state counter (exhaustions) and the caller counter (every repair). |
| 9 | minor | Both always-true assertions replaced with ones that can fail — the leakage test now proves a values-only scan *would* miss case 154, and the patient test asserts on result text rather than key names. Four tests added for findings 1 and 2. |
| 10 | minor | `channel` is a required keyword argument (see #1). |

Verified after the fixes: **168 tests passing**, offline.
