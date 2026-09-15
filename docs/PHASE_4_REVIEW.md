# Phase 4 review — the doctor panel

2026-09-15 — fresh-context review of `3a5507f`..working tree
(`src/agentclinic/graphs/doctor_panel.py`, `graphs/encounter.py`,
`agents/doctor.py`, `graphs/nodes.py` interactions, `cli.py`,
`config/prompts/{challenger,cost_steward}.md`, `tests/test_panel.py`,
`tests/graph_fixtures.py`).

Everything below was checked by compiling the graphs and running them on a fake
model, not by reading docstrings. `uv run pytest` is green: 197 passed.

## What I verified as sound

Recorded first, because three of these were adversarial-review findings and they
are genuinely closed.

- **The isolation claim is true, structurally.** Dumping the compiled subgraph's
  channels gives 14 channels and `encounter_log` is not among them. Instrumented
  `orchestrator` / `challenger` / `cost_steward` nodes, invoked with a parent
  state seeded with a sentinel event, received no key carrying that sentinel.
  This is a stronger property than `single_doctor` has: `build_solo_decide`
  passes the full `EncounterState` as its *internal* schema, so `encounter_log`
  exists there as an (empty) channel. The panel's `PanelState` removes it
  entirely.
- **`panel_events` arithmetic is correct end to end (C-6, C-7).**
  `BinaryOperatorAggregate` inside the subgraph, `LastValue` in the parent. The
  plain `panel_events: list[Event]` re-declaration in `PanelOutput` does **not**
  clobber the reducer — langgraph 0.6.11's `StateGraph._add_schema` silently
  tolerates a `LastValue` re-declaration of an existing channel. Three
  sequential writes accumulate in challenger → orchestrator → cost-steward
  order; nothing is dropped, nothing is duplicated. `panel_events` is absent
  from `PanelInput`, so each invocation starts from `[]` and a stale parent
  value cannot re-enter.
- **Unwritten output keys are omitted, not nulled.** When no node writes
  `parse_failures` or `stop_reason`, they are absent from the subgraph result,
  so the subgraph cannot clobber the parent's `stop_reason` with `None`.
- **The finalize re-deliberation is bounded to exactly one and cannot loop.**
  `challenged_this_finalize` is absent from `PanelOutput`, so the subgraph
  cannot reset it; `route_action` reads it pre-write; only an executed action
  clears it, and an executed action advances `turn` toward the cap. Confirmed by
  running the graph with an orchestrator that proposes `finalize` on every turn.
- **`absorb_panel`'s `panel_events` write is declared** (`state.py:115`) and
  cannot conflict with the subgraph's: `panel` and `absorb_panel` are joined by
  a plain edge and never run in the same superstep.
- **A finalize never escapes without a `FinalAnswer` on the budget path.** I
  raised `BudgetExceeded` at each of the first 12 structured calls of a panel
  encounter; all 12 produced a `FinalAnswer`, 9 with
  `stop_reason="budget_exhausted"`.
- **`challenge_due` / `cost_due` have no unreachable branches** and the turn
  arithmetic matches PLAN §2.3 (`turn > 0 and turn % 3 == 0 and not
  challenged_this_finalize`; fires on turns 3, 6, 9 …).
- **Ground truth does not reach a panel node.** Across 8 of the 29
  `dx_in_results` cases, `Correct_Diagnosis` reaches `encounter_log` and the
  `hypothesis` prompt (by design — the gatekeeper returns the data that contains
  it) and reaches **no** orchestrator, challenger or cost-steward prompt.
  `Management_and_Follow_Up` exists on one case (`medqa-0133`) and appears only
  on `JudgeView`; `doctor_view`, `patient_view` and `gatekeeper_view` carry
  neither field. The eval path passes `checkpointer=None`, and the tracer
  records token counts, never prompt text.

## Findings

### 1. The cost-steward's opinion can never reach the orchestrator, so half the panel is decision-inert

- **Severity:** blocker
- **File:** `src/agentclinic/graphs/doctor_panel.py:34-45` (with
  `src/agentclinic/graphs/nodes.py:23` and
  `src/agentclinic/agents/doctor.py:140-141`)
- **Problem:** `cost_objection` is in `PanelOutput` but **not** in `PanelInput`.
  The cost-steward runs *after* the orchestrator within one invocation, so the
  only way its objection can influence a decision is by crossing into the next
  invocation — which the input schema forbids. Two independent mechanisms block
  it, so fixing either alone is not enough:

  1. `PanelInput` omits `cost_objection`, so the channel starts unset on every
     invocation.
  2. `_CLEARS` (`nodes.py:23`) sets `cost_objection: None` in every action node,
     including `order_test` — the *only* action that can produce an objection —
     and that node runs immediately after `absorb_panel`, before the next
     `hypothesis` → `panel`.

  Measured: a panel run scripted with an objection carrying a unique marker
  produced 3 orchestrator prompts, **none** containing `## Cost review` and none
  containing the marker. The marker appeared only in `hypothesis` prompts, i.e.
  via the free-text `absorb_panel → encounter_log → summary` path. The
  `cost_objection` branch at `doctor.py:140-141` is dead code in both arms
  (`single_doctor` declares the key but has no writer for it).

  This contradicts D-025 twice over — "its objection informs subsequent
  orchestrator decisions" and "the orchestrator sees both opinions before
  deciding" — and applies only half of PLAN_REVIEW_3 C-2's accepted fix, which
  says in terms: give the subgraph **two** typed keys "that the orchestrator is
  explicitly allowed to read". PLAN §2.3's `input_schema` list has the same
  omission, so the code is faithful to one PLAN line that contradicts D-025 and
  C-2; the defect is inherited, not invented, but it ships here.

  The consequence for the experiment is the material part. Phase 4's acceptance
  criterion is "two additional sub-roles" (D-026). What is actually built is one
  advisory sub-role that works plus one that writes a comment into the
  transcript, so the headline panel-vs-single comparison attributes to "challenge
  *and* cost opinions" what is really "challenge opinions, plus one more line of
  transcript for `hypothesis` to paraphrase".
- **Fix:** two lines. Add `cost_objection: Any` to `PanelInput`, and stop the
  same turn's action node from wiping it before it is read — either drop
  `"cost_objection": None` from `_CLEARS` and let the next cost-steward
  overwrite it, or (cleaner, and keeps it one-shot) have `make_orchestrator_node`
  return `{"cost_objection": None}` alongside its decision once it has rendered
  the objection into the prompt, adding `orchestrator` to that key's
  `STATE_SOURCES` entry. Add a test that asserts `## Cost review` appears in the
  orchestrator prompt of the turn after an objection — see finding 6.

### 2. The recursion limit was never re-derived for the panel's loop, so a panel case crashes out of the accuracy denominator

- **Severity:** blocker
- **File:** `config/budgets.yaml:7` / `src/agentclinic/config.py:105`, against
  `src/agentclinic/graphs/encounter.py:86-94`
- **Problem:** `recursion_limit = max_turns * 6 + 20` (Q-27) was sized for a
  graph with 5 supersteps per executed turn. The panel adds 4 more supersteps
  (`challenger_final → hypothesis → panel → absorb_panel`) for every voluntary
  finalize attempt the orchestrator later backs away from. Measured by streaming
  `updates` at the shipped defaults:

  | graph | `max_turns` | limit | supersteps | result |
  |---|---|---|---|---|
  | `single_doctor` | 20 | 140 | 102 | completes, 38 spare |
  | `panel`, 9 re-deliberations | 20 | 140 | 138 | completes |
  | `panel`, 10 re-deliberations | 20 | 140 | — | **`GraphRecursionError`** |
  | `panel`, 7 re-deliberations | 10 | 80 | — | **`GraphRecursionError`** |

  Ten "propose finalize, get challenged, change your mind" events across 20
  turns is ordinary model behaviour, not a pathology. `run_one` catches the
  exception at `eval/runner.py:137`, records `outcome="crash"`, and the case
  leaves the accuracy denominator with no final answer.

  The bias direction is the problem. A case crashes precisely when the
  challenger repeatedly changes the orchestrator's mind — the exact phenomenon
  the panel arm exists to measure. The panel's accuracy is then computed over
  the subset of cases where the challenger was *least* effective, while
  `single_doctor` loses nothing. That confounds the headline comparison in a way
  no amount of n will fix.
- **Fix:** derive the limit from the graph, not from one constant. The panel
  needs `max_turns * 9 + headroom` in the worst case (5 per turn + 4 per
  re-deliberation, one per turn). Either add a
  `panel_recursion_limit_multiplier: 9` to `budgets.yaml` and select on
  `config`, or pass the multiplier into `load_budgets` from the CLI's `config`
  argument. Keep it derived in code per Q-27; do not hard-code a bigger number.

### 3. `StructuredOutputFailed` escapes the challenger, `challenger_final` and the cost-steward

- **Severity:** blocker
- **File:** `src/agentclinic/agents/doctor.py:272-273` and `:327-328`
- **Problem:** `budget_guarded` catches `BudgetExceeded` only. Neither advisory
  node catches `StructuredOutputFailed`, which `LLMCaller.structured` raises
  after its 3-attempt repair budget. Measured: a panel run whose challenger
  never returns a valid `ChallengerOpinion` propagates
  `StructuredOutputFailed` out of `graph.ainvoke`; the same failure in the
  single-doctor arm's orchestrator is caught at `doctor.py:155` and converted
  into a scored forced finalize (`stop_reason="parse_failure"`, `final`
  present, `parse_failures=3`).

  So the panel arm has three uncaught-exception sites (`challenger`,
  `challenger_final`, `cost_steward`) that the single arm does not, and each one
  turns a recoverable formatting failure by an **advisory** node into
  `outcome="crash"` for the whole case. Advisory means the encounter can proceed
  perfectly well with no opinion; there is no reason for this to be fatal.
  Combined with finding 2, the two arms are not comparable in their failure
  modes, and both asymmetries thin the panel's denominator.

  Related and worth fixing in the same edit: `STATE_SOURCES`
  (`src/agentclinic/graphs/state.py:128-129`) already declares `challenger`,
  `challenger_final` and `cost_steward` as writers of `parse_failures`. None of
  them ever writes it. The allow-list test only checks that the key set matches
  `EncounterState`, so a declared writer that does not exist passes silently —
  worth knowing about that test's real strength.
- **Fix:** wrap both `caller.structured` calls in
  `except StructuredOutputFailed as exc:` and return
  `{channel: [Event(turn=turn, kind="parse_failure", actor="system", text=...)],
  "parse_failures": exc.attempts}` with no opinion key. The orchestrator then
  simply sees no opinion this turn, which is the correct degradation for an
  advisory role, and `STATE_SOURCES` becomes true.

### 4. `agentclinic judge` writes a function object into the report title

- **Severity:** major
- **File:** `src/agentclinic/cli.py:473`
- **Problem:** the diff changed `config_name="single_doctor"` to
  `config_name=config` in **both** `run` and `judge`. `run` gained a `config`
  parameter; `judge` did not. Inside `judge`, `config` resolves to the
  module-level `@app.command() def config()` at `cli.py:72`. `RunMetadata` is a
  plain dataclass, so nothing validates it. Reproduced:

  ```
  # Run r — <function config at 0x10fd50540>
  ```

  `config_name` is also used for the comparison block heading
  (`eval/report.py:135`) and the coverage line (`:138`). `judge` is the command
  the project actually uses to re-score a run (D-047 hand-assigned verdicts go
  through `--verdicts`), so this corrupts the header of the artefact that
  carries the headline number.
- **Fix:** give `judge` its own
  `config: str = typer.Option("single_doctor", help="single_doctor or panel")`,
  or parse it out of `run_id` (which is already
  `f"{split}-{config}-{hex}"`). A `str` annotation on `RunMetadata.config_name`
  that is actually enforced would have caught this; a cheap guard is
  `assert isinstance(config, str)` at the call site.

### 5. The panel's request projection under-counts by ~53%

- **Severity:** minor
- **File:** `src/agentclinic/cli.py:264-265`
- **Problem:** `per_turn = 4 if config == "panel" else 3` projects 83 requests
  per panel case at `max_turns=20`. Measured worst case on a scripted model:

  | scenario | calls, 20 turns |
  |---|---|
  | single, no `order_test` | 41 (projected 63) |
  | panel, no `order_test`, no re-deliberation | 47 |
  | panel, `order_test` every turn | 67 |
  | panel, `order_test` + one re-deliberation every turn | **127** (projected 83) |

  The comment two lines above says exactly why this matters: "Refusing up front
  is far better than discovering the cap mid-run, where a daily-cap 429 turns
  every remaining case into an `error` and silently shrinks the accuracy
  denominator." An under-projection defeats the guard. It does not bite at the
  committed n=3 (3 × 127 = 381 < 1000) but bites from n ≈ 8.
- **Fix:** `per_turn = 7` for the panel (hypothesis + challenger + orchestrator
  + cost-steward, plus challenger_final + hypothesis + orchestrator on the
  re-deliberation), or state in the comment that the figure is a typical-case
  estimate and add a margin.

### 6. Three panel tests cannot fail, and one of them stands in for behaviour that is actually broken

- **Severity:** minor
- **File:** `tests/test_panel.py:175-184`, `:187-194`, `:23-29`, `:161-170`
- **Problem:**
  - `test_a_cost_objection_does_not_block_the_order` asserts only that the test
    was performed and that a `cost_objection` event was logged. Neither can fail
    given the design: no node routes, and `absorb_panel` folds every
    `panel_events` entry into the log. It reads as coverage of D-025's advisory
    semantics, but D-025's other half — "its objection informs subsequent
    orchestrator decisions" — has no test at all, and is broken (finding 1).
  - `test_the_subgraph_cannot_see_the_encounter_log` asserts that
    `challenger_opinion` is in **both** schemas and is silent about
    `cost_objection`. The asymmetry that is the bug is visible in the test and
    unasserted. It also inspects `__annotations__` rather than the compiled
    graph, so it would pass if `build_doctor_panel` were changed to pass
    `PanelState` as its `input_schema`; that case is caught only incidentally by
    `test_the_panel_orchestrator_never_sees_raw_event_text`.
  - `test_a_cap_forced_finalize_skips_the_challenger` is structurally
    unfailable: the cap path reaches `finalize` through `route_stop`, which never
    consults `route_action`, so no challenger node is reachable there regardless
    of the code under test. It is a regression guard against re-adding
    `challenger_stop`, nothing more — worth saying so in the docstring. It also
    uses `max_turns=2`, so `turn` never reaches 3 and the scheduled challenger
    could not have fired anyway.
  - `test_absorb_panel_clears_its_channel` calls the node as a function, so it
    would pass if the **parent**'s `panel_events` gained an `add` reducer, which
    is the mutation that would make the `[]` write a no-op. That case is caught
    only by `test_three_panel_turns_leave_the_log_unduplicated_and_complete`.
- **Fix:** add the missing assertion for finding 1 (an orchestrator prompt
  containing `## Cost review` on the turn after an objection); assert
  `cost_objection in PanelInput.__annotations__`; assert
  `"encounter_log" not in build_doctor_panel(...).builder.channels` so the
  structural claim is tested against the compiled graph; and note in the
  cap-path docstring that the assertion is a guard against a deleted node rather
  than a property of the current router.

### 7. The scheduled challenger and `challenger_final` are indistinguishable in the trace

- **Severity:** minor
- **File:** `src/agentclinic/agents/doctor.py:269-273`, `:293-301`
- **Problem:** `make_challenger_final_node` reuses `make_challenger_node`, which
  hard-codes `node="challenger"` in the `caller.structured` call, and both write
  `Event(kind="challenge")`. Nothing in the trace or in `_derive(events)`
  separates "the every-third-turn challenge" from "the pre-finalize challenge".
  The one number the panel arm most wants to report — how often the
  re-deliberation changed the decision — is not derivable from what is recorded,
  and `test_turn_three_runs_both_the_scheduled_and_the_final_challenger` has to
  disambiguate by counting events at a turn rather than by kind.
- **Fix:** thread the `node` label through `make_challenger_node` alongside
  `channel` (`node="challenger_final"` from the wrapper), and set
  `meta={"position": "scheduled" | "pre_finalize"}` on the event.

## Verdict

**NO-GO.**

Three blockers must change before a panel run is worth spending requests on:

1. **Finding 1** — add `cost_objection` to `PanelInput` and stop the same turn's
   action node clearing it. Until then the panel is one advisory sub-role, not
   two, and the comparison does not measure what PLAN §1.2 and D-025 say it
   measures. Two lines plus a test.
2. **Finding 2** — re-derive `recursion_limit` for the panel's loop shape.
   Until then the panel silently drops exactly the cases where the challenger
   works, which is a confound in the headline number rather than a crash to
   notice and retry.
3. **Finding 3** — catch `StructuredOutputFailed` in the two advisory nodes. An
   advisory role must degrade to "no opinion", never to a lost case, and the
   asymmetry with `single_doctor`'s orchestrator is itself a comparison defect.

Finding 4 should go in the same pass — it is a one-line fix to a command that
writes the project's reporting artefact.

Everything the adversarial reviews specifically predicted would recur —
the `encounter_log` boundary (C-2's isolation half), the `panel_events` reducer
(C-6), `absorb_panel`'s clear (C-7), and the one-shot bound on the finalize
re-deliberation (C-1) — is correctly implemented and holds under test. The
defects above are all in what was *not* re-derived when the second loop was
added: the typed input schema for the second opinion, the recursion budget, the
request budget, and the failure handling.

---

# Resolutions

Appended 2026-09-15. **All 7 findings accepted.** 202 tests passing.

| # | Severity | Resolution |
|---|---|---|
| 1 | blocker | **accepted** → D-049. `cost_objection` added to `PanelInput`; `_CLEARS` no longer touches either opinion; the **orchestrator** clears them after rendering, making an opinion one-shot. New test asserts `## Cost review` and a unique marker appear in the *next* turn's orchestrator prompt and not the current one — the assertion whose absence let this ship. PLAN.md §2.3's schema list is corrected, since the omission was inherited from it. |
| 2 | blocker | **accepted** → D-048. `recursion_limit_multiplier` is now per graph (`single_doctor: 6`, `panel: 10`), and `load_budgets(graph=...)` raises on an unknown graph rather than defaulting. Test asserts the panel's limit exceeds the single doctor's. |
| 3 | blocker | **accepted** → D-049. An `advisory()` wrapper converts `StructuredOutputFailed` in the challenger, `challenger_final` and cost-steward into `None` plus a `parse_failures` delta. Test scripts six consecutive invalid outputs across both challenger positions and asserts the case still produces a `FinalAnswer`. |
| 4 | major | **accepted.** In `judge`, the bare name `config` resolved to the module-level `@app.command() def config()`. The configuration is now read back from the run id, which encodes it (`dev-panel-abc123`). |
| 5 | minor | **accepted.** `per_turn` for the panel is 7, not 4 — the measured worst case is 127 calls at 20 turns, not 83. An under-projection defeats the guard it exists to be. |
| 6 | minor | **accepted** (all four). The structural claim is now asserted against the **compiled graph**'s channels, not `__annotations__`; both opinion keys are asserted present in both schemas; the cost-objection test now asserts the behaviour D-025 actually promises; and the cap-path test's docstring says plainly that it is a regression guard against re-adding `challenger_stop`, not a property of the router. |
| 7 | minor | **accepted.** Challenge events carry `meta.when` = `scheduled` or `pre_finalize`. |

## Note on finding 1

The review is right that the defect was inherited: PLAN.md §2.3's `input_schema`
list omitted `cost_objection`, and the code faithfully implemented it. Three
prior review rounds read that line without catching it, and the Phase 4 tests
covered the half of D-025 that worked ("the order proceeds anyway") while the
half that was broken ("its objection informs subsequent decisions") had no test
at all. A passing suite and a reviewed plan agreed with each other and were both
wrong.
