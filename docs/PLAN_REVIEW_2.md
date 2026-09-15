# Adversarial review of docs/PLAN.md — Round 2

2026-09-15 — fresh-context review of revision 2

Scope: `docs/PLAN.md` (rev 2), `docs/DECISIONS.md` (D-001…D-033), `docs/BRIEF.md`,
`docs/PLAN_REVIEW.md` (round 1 + resolutions), `docs/DECISIONS_OPEN_2.md` (context),
and read-only verification against `dataset/agentclinic_medqa_extended.jsonl`
+ `dataset/MANIFEST.json`. No `src/`, `scripts/`, `tests/` or `PHASE_0_NOTES.md`.

### Dataset claims I re-derived and **confirmed** (so they are not findings)

| Plan claim | Where | Verified |
|---|---|---|
| 214 non-blank lines, single top-level key `OSCE_Examination` | §7 | ✅ |
| `dx_in_results` = **29** under the §3.2 rule (casefold, `_`→space, collapse ws, strip trailing parenthetical, flatten **keys and values** of `Test_Results` + `Physical_Examination_Findings`) | §3.2 | ✅ 29 — ids 2, 3, 11, 14, 18, 20, 23, 39, 48, 52, 62, 86, 87, 102, 104, 107, 108, 119, 134, 144, 154, 155, 161, 163, 166, 174, 185, 197, 199 |
| 27 values-only / 28 keys+values without stripping | §3.2 | ✅ 27 / 28 |
| Diagnosis in **zero** `Physical_Examination_Findings` | §3.2 | ✅ 0 |
| Leak-free denominator **185** | §3.2 | ✅ 214 − 29 |
| Case 154 `Varicella` has key `Varicella_Specific_Tests`; case 104 `Legg-Calvé-Perthes disease (LCPD)` appears without the suffix | §3.2 | ✅ both |
| 16 cases with top-level string values (2 all-string 150/187, 14 mixed), 2 cases with lists (153, 185) | §4.2 | ✅ exactly |
| 4 cases `Test_Results == {}` (69, 106, 111, 209) | §4.2 | ✅ |
| 234 distinct top-level `Test_Results` keys, 165 singletons | R7 | ✅ |
| 35 case-insensitive duplicate-diagnosis groups over 72 cases; 31 over 63 raw | §6.1 | ✅ |
| **§6.1 split algorithm, traced by hand** | §6.1 | ✅ produces **exactly dev=40 / heldout=174** |
| Dev contains **zero** empty-`Test_Results` cases and **5** `dx_in_results` cases | §6.2, D-029 | ✅ dev flagged = {2, 39, 104, 108, 119} |
| **§6.2 3-case rule, traced by hand** | §6.2 | ✅ yields case ids **2, 9, 12** (`medqa-0002/0009/0012`) |
| `Management_and_Follow_Up` on exactly 1 case | D-016 | ✅ line 133 |
| Diagnosis never in `Objective_for_Doctor` / `Patient_Actor`, even normalised + parenthetical-stripped | §1.1 | ✅ 0 / 0 |

**What is genuinely fixed.** §6.1's split is now a real, deterministic algorithm
and I reproduced 40/174 exactly (round 1 #19 — the most dangerous finding of that
batch — is closed). §3.2's `dx_in_results` rule is correct, computed, and its 29
matches. §2.1's `objective` key, the `JudgeVerdict` cleanup, `entry_matches`,
`proposed_action`'s removal, D-025/D-026/D-027/D-028/D-029/D-032's recorded
deviations, and the three-plus-one isolation tests are all sound. The corrected
isolation property in §1.1 is the right property and is actually provable.

The remaining problems cluster, as expected, in the parts revision 2 rewrote:
the §2.1 single-writer table, the §2.3 panel graph, and the subgraph schemas.

---

## Part A — status of round 1 findings

| # | R1 severity | Status | Note |
|---|---|---|---|
| 1 | blocker | **new defect introduced** | The loop is gone, but `challenged_this_finalize` cannot express the condition `redec` is specified to test — see B-1. |
| 2 | blocker | resolved | Property restated correctly and testably. One residual instance of the discredited wording survives in §3.3 item 4 — see B-15. |
| 3 | blocker | resolved | D-025; `should_reopen` / `replacement_action` dropped, single `action` key. |
| 4 | blocker | resolved | D-026, recorded deviation in §10 and R10. |
| 5 | blocker | resolved | D-027 drops the rule-based arm; no vitals leak path remains. |
| 6 | blocker | **partial** | The *rule* is decided (D-028). The *arithmetic* it creates is not: `n_scored` can be 0 or 1 at n=3; coverage, Wilson and paired-bootstrap denominators and pairing are undefined — see B-9. |
| 7 | blocker | resolved | D-029; I traced the rule and it yields {2, 9, 12}. Unstated consequence in B-17. |
| 8 | major | **partial** | Primary rule and count verified correct. The *secondary* `dx_tokens_in_results` count (5, incl. 182) is not reproducible — see B-12. |
| 9 | major | resolved | Three tests + the positive leak test, named in §3.3 and in Phase 1 acceptance. |
| 10 | major | **partial** | Asymmetry is genuinely gone (both graphs are now hypothesis-first). But the accepted fix "forced stops route through a final `hypothesis` update" was silently dropped, and a new asymmetry appears: the panel's re-decision runs `hypothesis`, which is the sole writer of `turn`, so the `turns` metric counts different things in the two graphs. |
| 11 | major | resolved | `objective` key added and exempted; `Event`/`EncounterSummary`/`RedFlag`/`StopReason` defined; summary producers named. |
| 12 | major | **partial / new defect** | Unit and graceful-stop wording fixed, but `BudgetExceeded` is caught only at the orchestrator while six other node types call the LLM, and in the panel the orchestrator cannot write `stop_reason` at all — see B-3, B-4. The "cap is inert under free models" half was deferred to #13 and never addressed in §5's stop-condition table. |
| 13 | major | **partial** | Daily counter, pre-flight and 429 classification all landed. But §5 ("all stop conditions produce a `FinalAnswer`") and §5.1 ("a daily-cap breach **aborts the run**") now contradict each other, and the minute-cap retry budget is still 7 s against a 60 s window — see B-6. |
| 14 | major | **partial / new defect** | `red_flag_turn` fix is correct. The single-writer table it added is itself wrong for `spend_usd`/`parse_failures`, silent on delta-vs-absolute semantics, contradicts §5 item 4 on `stop_reason`, and leaves `turns_since_challenge` with no resetter — see B-4, B-5. |
| 15 | major | **partial** | The per-run rule is stated in a comment; the code directly beneath it still declares a static 5-member `Literal`, and a model-emitted disabled action is an unhandled route — see B-8. |
| 16 | major | resolved | One judge call returns `match_type` + `entry_matches`; top-k computed from booleans. |
| 17 | major | resolved | `correct` removed; both flags derived. |
| 18 | major | **partial** | Top-level/sub-tree rule and `str\|dict\|list` handling are right. "A request naming a leaf matches its parent" has no tie-break and is ambiguous in 9 verified cases — see B-13. PEF lists (37, 103) are missing from the acceptance criteria. |
| 19 | major | resolved | Algorithm traced by hand: exactly 40/174, grouping case-insensitive. Three small defects in how it is *written* — see B-14. |
| 20 | major | resolved | D-030 pins `order: ["Nvidia"]`. Its dev-run fallback benefit is void (single endpoint) — noted in B-16. |
| 21 | major | **partial** | D-031 defines the set and a bar. Who labels is unstated, the bar is a disjunction, and the stated consequence is unbounded prompt tuning against the only validation data — see B-11. |
| 22 | major | resolved | D-032 pulls the skeleton into Phase 2. |
| 23 | minor | resolved | D-033 wording; user action recorded in §8. |
| 24.1 | minor | resolved | Matrix cell ✅, agrees with §3.3 item 1. |
| 24.2 | minor | **partial** | §4.1 wording fixed. The panel diagram's `route -->\|3 attempts invalid\| fin` edge bypasses `challenger_final`, contradicting §2.3's "before **every** finalize". |
| 24.3 | minor | resolved | §5 item 10. Destination of the recorded traceback is unspecified — B-17. |
| 24.4 | minor | **still broken** | The accepted `--no-report --cache` iteration mode appears **nowhere** in PLAN.md rev 2, yet D-030 conditions provider fallbacks on "`--cache` / `--no-report` development runs". See B-16. |
| 24.5 | minor | **new defect introduced** | The key lists landed — and putting `encounter_log` in both of them creates a reducer duplication bug. See B-2. |
| 24.6 | minor | resolved | `AsyncAnthropic`. |

**Not fully resolved: 11 of 24** (#1, 6, 8, 10, 12, 13, 14, 15, 18, 21, 24 —
the last counting 24.2/24.4/24.5).

---

## Part B — new findings

## 1. `challenged_this_finalize` cannot express the condition `redec` is specified to test

- **Severity:** blocker
- **Section:** §2.3 ("Termination of the finalize path"), §2.1 single-writer table
- **Problem:** §2.3 says: "`challenger_final` **sets** `challenged_this_finalize = True`.
  `redec` returns `PANEL` only if the flag **was previously false** and no cap has
  been hit." `redec` is a conditional edge evaluated *after* `challenger_final`
  has already written the flag, and LangGraph routing functions read state — the
  pre-write value no longer exists anywhere. §2.1 confirms only two writers
  (`challenger_final` sets, action nodes clear) and no second key.
  Two engineers implement this differently and both are defensible readings:
  - **(a)** `redec` reads `challenged_this_finalize`; it is `True` on every
    evaluation because `challenger_final` just set it, so `redec` always routes to
    `finalize`. The `redec → PANEL` edge in the §2.3 diagram is dead code, the
    "exactly one re-decision" of D-025 **never happens**, and `challenger_final`
    becomes a node whose opinion is appended to `encounter_log` immediately before
    the encounter ends — no node ever reads it. The panel's entire substantive
    difference from `single_doctor` on the finalize path evaporates, silently, and
    the Phase 4 acceptance test ("`challenged_this_finalize` bounds the finalize
    path") **passes**, because it does bound it — at zero.
  - **(b)** The engineer invents an undeclared second key or makes
    `challenger_final` write the flag only on its second execution, producing the
    intended one re-decision. This is a state key that no section of the plan
    declares, which the §3.3 allow-list test is specifically designed to reject.
  This is the mechanism D-025 was chosen for ("bounded by construction, no counter
  to get wrong"). As written it is not implementable as intended.
- **Fix / Question:** The information "has a challenge already happened *in this
  finalize attempt*" must survive `challenger_final`'s own write. Either (i)
  `redec` is replaced by routing *out of* `route_action`: `route_action` sends
  `finalize` to `challenger_final` when `challenged_this_finalize == False` and
  straight to `finalize` when it is `True`, with `challenger_final`'s only outgoing
  edge going back to `PANEL` — one flag, correct, still no counter; or (ii) accept
  a counter (`finalize_attempts: int`, incremented by `challenger_final`, `redec →
  PANEL` iff `finalize_attempts == 1`). Whichever is chosen, §2.1's writer table
  must match, and the Phase 4 test must assert the re-decision *happens* once, not
  only that the path terminates.

## 2. `encounter_log` in both subgraph schemas duplicates the entire log on every panel entry

- **Severity:** blocker
- **Section:** §2.3 (subgraph `input_schema` / `output_schema`), §2.1
- **Problem:** `encounter_log` is `Annotated[list[Event], operator.add]` in the
  parent, and §2.3 lists it in **both** the panel subgraph's `input_schema` and its
  `output_schema`. A compiled subgraph added as a node returns its **final state**
  for the declared output keys — not a delta. So the parent receives
  `parent_log + new_events` as the node's update and applies it through
  `operator.add`, yielding `parent_log + (parent_log + new_events)`. The pre-existing
  log is duplicated on **every** entry into `PANEL`, and `PANEL` is re-entered once
  per turn, so the log roughly doubles each turn (≈2ⁿ growth over a 20-turn cap).
  §2.1's rule — "nodes return **only new events**, never the accumulated list" —
  cannot be honoured by a subgraph-as-node, so the plan contradicts itself here.
  Consequences are not cosmetic: `EncounterSummary.findings` and `tests_ordered`
  are "derived from `encounter_log` by hypothesis", so duplicated gatekeeper text
  (which for the 29 flagged cases contains the diagnosis) is re-fed into the
  doctor's context repeatedly; `red_flag_turn` is derived from the log (the
  round-1 #14 fix) and will report the wrong turn; every
  `traces/<case_id>.jsonl` is corrupted; and context/token cost explodes against
  the very request budget D-022 exists to conserve.
- **Fix / Question:** Do not let a reduced channel cross the boundary in both
  directions. Either (i) remove `encounter_log` from the subgraph's `input_schema`
  — the subgraph then starts empty and returns only its own new events, which is
  exactly what `operator.add` wants — and give `hypothesis` whatever read-only view
  of history it needs through a *separate*, last-write key (e.g. the existing
  `summary`); or (ii) keep the subgraph's log in a distinct local key
  (`panel_events`) and map it into `encounter_log` in a wrapper node. Add a test
  that runs three panel turns and asserts `len(encounter_log)` equals the number of
  events actually emitted.

## 3. `stop_reason` has no writer that can legally write it, and three of its five values have no writer at all

- **Severity:** blocker
- **Section:** §2.1 (writer table), §2.3 (subgraph boundary), §5 item 4, §6.3
- **Problem:** Three statements in revision 2 cannot all hold:
  1. §2.1: `stop_reason`'s **sole writer is `finalize`**.
  2. §5 item 4: on `BudgetExceeded` "the **orchestrator** ... sets `stop_reason`
     and routes to `finalize`".
  3. §2.3: "`case_id`, `test_cost_usd`, **`stop_reason`** and `final` never cross
     the boundary" — and the orchestrator lives **inside** the panel subgraph.
  So in the panel graph the orchestrator physically cannot write `stop_reason`; in
  `single_doctor` it can, but then `finalize` (which runs afterwards and is the
  declared sole writer, last-write-wins) overwrites it — and `stop_reason` is
  reported per case in §6.3 along with `forced_stop`. Every forced stop would be
  reported as `stop_reason = "finalize"`, i.e. the forced-stop rate that Q-24 asks
  to be reported is systematically zero.
  Separately, `StopReason = Literal["finalize","turn_cap","spend_cap","request_cap","crash"]`
  but `should_continue` and `redec` are **routing functions** and cannot write
  state, so `turn_cap` can only be reached if `finalize` re-derives it from `turn`;
  `request_cap` is a process-global condition not present in state at all; and
  `crash` means the graph raised, so `finalize` never ran and the key is `None`.
  Three of five values are unreachable by the declared writer.
- **Fix / Question:** Make the stop reason an explicit, single-writer decision:
  add a `finalize_reason` (or reuse `stop_reason`) that is written by a small
  **node** — not a routing function — placed on each stop edge, and declare it in
  §2.1 and in the subgraph `output_schema` if the orchestrator must set it. Then
  state how `crash` and `request_cap` are recorded (they are runner-level, not
  state-level: say so, and drop them from `StopReason` or mark them runner-only).

## 4. `BudgetExceeded` is caught at the one node that is not the problem

- **Severity:** major
- **Section:** §5 item 4, §2.1, §2.3
- **Problem:** §5 item 4 says a `BudgetExceeded` "raised by the client is caught by
  the orchestrator". But the orchestrator is one of at least seven LLM call sites:
  `hypothesis`, `challenger`, `challenger_final`, `cost_steward`, `finalize`,
  `ask_patient` (patient agent), and the gatekeeper's tier-4 LLM fallback
  (§4.2). A breach raised in any of those propagates out of the graph, so the
  encounter produces no `FinalAnswer` — contradicting §5 item 4's own headline
  ("Stop conditions — **all produce a `FinalAnswer`**"). Worst case is `finalize`
  itself: there is no recovery node after it.
  Compounding this in the panel: even when the orchestrator *does* catch it, it can
  only set `action = "finalize"` (it cannot set `stop_reason`, see B-3), and
  `route_action` sends `finalize` to **`challenger_final`** — an additional LLM call
  made *after* the budget was declared exhausted, which raises again, uncaught.
- **Fix / Question:** Catch `BudgetExceeded` where it can be handled: either wrap
  every LLM-calling node in a common decorator that converts it into a state update
  (`action="finalize"`, budget flag set) with no further call, or hoist the guard
  out of the client into `should_continue`/`route_action` as a pre-condition on
  state. Say explicitly that `challenger_final` is skipped when the budget flag is
  set, and that `finalize` under a budget breach is constructed in Python from the
  existing `differential` with **no** model call.

## 5. The single-writer table is false for `spend_usd` and `parse_failures`, and never says whether writes are deltas or totals

- **Severity:** major
- **Section:** §2.1
- **Problem:** The table names `orchestrator` as the **sole writer** of `spend_usd`
  and `parse_failures`. Both are properties of *every* LLM call: `hypothesis`,
  `challenger`, `cost_steward`, `finalize`, `ask_patient` and the gatekeeper
  fallback all spend tokens, and all six emit structured output subject to Q-15's
  3-attempt repair loop, so all six can produce parse failures. Two readings, with
  different numbers:
  - **(a)** The table is a *requirement*: only the orchestrator writes these keys,
    copying a running total maintained inside the OpenRouter client. Then the table
    is honest but `spend_usd` in state is stale for every non-orchestrator call, the
    spend cap is evaluated against a lagging figure, and a parse failure inside
    `ask_patient` or `finalize` is never counted in `parse_failures` — the very
    metric §6.3 reports and D-028 uses to assign outcome `error`.
    In `single_doctor`, `finalize` is the last node, so its cost and its parse
    failures are **never** recorded at all.
  - **(b)** The table is aspirational and each node writes its own contribution.
    Then "sole writer" is false, last-write-wins silently drops concurrent writes,
    and the §3.3 allow-list test (which pairs each key with one declared source) is
    built on a false premise.
  The table also never states the convention for accumulating keys. `encounter_log`
  is explicitly a delta ("only new events"); by contrast `spend_usd`,
  `test_cost_usd`, `parse_failures`, `turn` and `turns_since_challenge` could each
  be written as an absolute running total or as a delta with an `operator.add`
  reducer. Nothing in §2.1 says which, and the two produce different values.
- **Fix / Question:** Decide and write down: (i) cost and parse-failure accounting
  is owned by the client/tracer, and the state keys are *snapshots* refreshed by a
  named node (say which, and accept that post-snapshot calls are excluded); or (ii)
  give `spend_usd`, `test_cost_usd` and `parse_failures` `operator.add` reducers and
  let every node return its own delta. Either way add a "value semantics" column to
  the §2.1 table (delta vs absolute) alongside the reducer column.

## 6. `turns_since_challenge` has no resetter, so the challenger schedule is undefined

- **Severity:** major
- **Section:** §2.1, §2.3 ("Sub-role scheduling")
- **Problem:** §2.3 schedules the challenger "every 3rd turn inside the subgraph".
  §2.1 names `hypothesis` as the **sole writer** of `turns_since_challenge`. The
  `challenger` node is therefore forbidden from resetting it. Two implementations:
  - **(a)** `hypothesis` increments it monotonically and `challenge_due` tests
    `turns_since_challenge % 3 == 0`. Then the key is just `turn` under another
    name, the "since_challenge" semantics are a lie, and the schedule drifts
    whenever a finalize re-decision inserts an extra `hypothesis` execution
    (B-1) — the challenger can fire twice in consecutive real turns.
  - **(b)** The `challenger` resets it to 0, which violates the single-writer table
    and the allow-list test.
  There is also no rule for the first turn: at `turn == 0` or `1`, is a challenge
  due? Under (a) with `% 3`, turn 0 fires a challenge before any information exists.
- **Fix / Question:** Pick one. If the schedule is genuinely "every 3rd turn", drop
  `turns_since_challenge` entirely and route on `turn % 3 == 0 and turn > 0`,
  removing a key and a writer conflict. If it is genuinely "3 turns since the last
  challenge", name `challenger` as its writer in §2.1 and state the initial value.

## 7. Per-node state visibility inside the panel is enforced by prompt construction, not by state design

- **Severity:** major
- **Section:** §2.3 (single `input_schema` for the subgraph), §3.1 matrix, §5 item 7
- **Problem:** Brief §5 requires that "information isolation must be enforced by the
  state design, not by prompts". The closure-bound `CaseStore` views do that for
  *case* fields, and that part is sound. But the §3.1 matrix is a **per-node** table,
  and the second column of information — what each node sees of the accumulated
  encounter — has no per-node enforcement at all: the panel declares **one**
  `input_schema` shared by `hypothesis`, `challenger`, `orchestrator` and
  `cost_steward`, so all four can read every listed key including the raw
  `encounter_log`.
  This collides directly with §5 item 7: "the doctor sees `EncounterSummary`; raw
  messages live in traces and are **never resent**". Two engineers will build
  materially different systems from these two sentences — one builds the
  orchestrator prompt from `summary` only (honouring §5 item 7 and the ~2000-char
  caps), the other from `encounter_log`, which is right there in the node's state
  and is the only place the verbatim gatekeeper text lives. For the 29 flagged
  cases that choice determines whether the doctor sees the diagnosis string once, or
  on every subsequent turn for the rest of the encounter — i.e. it changes the
  magnitude of the one leak the project is measuring, and `dx_in_results` accuracy
  (a headline number in §6.4) with it.
- **Fix / Question:** State in §2.3 which node reads `encounter_log` and forbid the
  others in code, not in a prompt file — e.g. `hypothesis` reads the log (it must,
  to derive `findings`/`tests_ordered`) and the orchestrator/challenger/cost-steward
  receive a state object that does not contain it. In LangGraph that means either
  distinct node-level input filtering or nesting `hypothesis` in its own subgraph.
  Add a test asserting the orchestrator's rendered prompt contains no substring of
  any `Event.text` that is absent from `summary`.

## 8. The per-run `Action` set is a comment above a static `Literal`, and a disabled action has no route

- **Severity:** major
- **Section:** §4.1, §2.2/§2.3 `route_action`, §7 Phase 3/5
- **Problem:** §4.1 says "Action set is built per run from the enabled actions" and
  then declares `Action = Literal[...]` with all five members, including
  `search_literature`, unconditionally. `Action` is a field type on
  `OrchestratorDecision`, i.e. it is baked into the JSON schema sent to the model at
  import time. Two readings:
  - **(a)** Literal, built dynamically (`create_model`) per run. Then the shown code
    is wrong, and every module that imports `Action` for type hints gets a different
    type per run.
  - **(b)** Static Literal; the enabled set is communicated to the model **in the
    prompt**. Then in Phases 3–4 (and in the brief §8 "without the evidence agent"
    arm, and whenever `NCBI_EMAIL` is unset per Q-18) the model can legitimately
    emit `search_literature`, the structured output validates, `route_action`
    receives a value it has no edge for, raises `RoutingError`, and the case is
    recorded as `crash` — dropping it from the accuracy denominator. At n=3 one such
    event is 33 points. This is the exact "routing function with an unhandled return
    value" class the brief asks about, and it is reachable by the *model behaving
    correctly*, not by a bug.
  §4.1 also never says at what granularity the structured-output fallback applies
  ("drop to `response_format` JSON mode, then to prompt-and-parse"): per call
  (silent per-case degradation, mixed modes within a run, mixed `parse_failures`
  rates) or per run (decided once by the Phase 1 spike and recorded in metadata).
- **Fix / Question:** Choose (a) and show it — `make_action_type(enabled)` returning
  the `Literal`, with `OrchestratorDecision` built per run — or choose (b) and give
  `route_action` an explicit branch for a valid-but-disabled action that records a
  `parse_failure` event and re-prompts rather than raising. Also state whether the
  structured-output fallback is a per-run decision (recommended, so a run is
  internally comparable) and record the resolved mode in run metadata.

## 9. D-028's variable denominator has no defined arithmetic at the only scale that will occur

- **Severity:** major
- **Section:** §6.3, §6.4, D-028
- **Problem:** D-028 excludes `abstained`, `error` and `crash` from the accuracy
  denominator. With n=3 that denominator is 3, 2, 1 or **0**, and the plan defines
  the behaviour for none of those:
  1. **n_scored = 0.** Accuracy is 0/0. Wilson's interval is undefined. The report
     must print *something*; nothing says what.
  2. **Coverage's own denominator is undefined.** Is coverage
     `n_scored / (n_scored + n_abstained)` (excluding errors, which are a harness
     property, not a clinical one) or `n_scored / n_total`? The two differ by 33
     points whenever one case errors.
  3. **The paired bootstrap breaks.** §6.4 reports "panel-minus-single with a paired
     bootstrap". Pairing requires the same case scored in both arms. Under D-028 a
     case can be `scored` in the single-doctor arm and `abstained`/`error` in the
     panel arm. Nothing says whether such a pair is dropped (resampling over n≤2
     pairs), imputed, or counted as a difference.
  4. **The headline metric is gameable in the direction the project is measuring.**
     Panel-vs-single is the project's central comparison, and abstention-excluded
     accuracy rewards whichever arm abstains more. The challenger's entire job is to
     raise doubt, so the panel is *systematically* the arm more likely to abstain.
     The plan reports coverage beside accuracy but never says the comparison must be
     made at matched coverage, or that the difference is uninterpretable otherwise.
- **Fix / Question:** Write the arithmetic into §6.3: define coverage's denominator;
  define the printed value and interval when `n_scored ∈ {0, 1}` (e.g. "n/a
  (n_scored=0)", never `0.0`); define the pairing rule for the bootstrap (recommend:
  restrict to cases scored in **both** arms, and print that pair count beside the
  difference); and state in §6.4 that panel-minus-single accuracy is reported
  together with panel-minus-single **coverage**, and is not interpreted when the
  coverages differ.

## 10. Two numeric limits are still undecided, so §8's "no open questions" is false

- **Severity:** major
- **Section:** §5.1, §4.2, §8
- **Problem:** This is the same class as round-1 #20 (a decision recorded without
  the value that makes it a specification), and two instances survive:
  1. **The judge's per-run call cap has no number.** §5.1: "The judge is exempt from
     these guards (D-021) but has its own per-run **call cap** (D-031)". D-031:
     "a per-run judge **call** cap is added." No value anywhere. It is the only
     limit of any kind on the judge, which runs on subscription auth with no price
     signal (R5), so its absence means the judge is effectively uncapped.
  2. **The `rapidfuzz` threshold has no number and no tuning procedure.** §4.2:
     "`rapidfuzz` above threshold ... Threshold tuned on dev only." There is no
     labelled set of (doctor request → correct key) pairs anywhere in the plan, so
     there is nothing to tune *against* and no stated objective (maximise match
     rate? minimise wrong-key matches? they trade off directly, and a wrong-key
     match hands the doctor results it did not order — brief §5.2's explicit
     prohibition). This is a live isolation-relevant knob with no value and no
     method.
  Worse, "tuned on dev only" does not protect anything here: **every reported run is
  on dev**. §6.2 draws the 3 evaluation cases from dev; I verified they are cases 2,
  9 and 12, all in dev. So the gatekeeper is tuned on the same three cases it is then
  measured on. The dev/held-out machinery is real and correct (§6.1), but it buys
  nothing until evaluation moves to `--split heldout`, and the plan never says so.
- **Fix / Question:** Put to the user: *"(a) What per-run judge call cap? (b) What
  rapidfuzz threshold, and tuned against what labelled examples with what objective
  — or should tier 3 be dropped so the cascade is exact → synonyms → LLM-over-keys,
  removing an unspecifiable knob?"* And add a sentence to §6.2: all Phase 3–5
  numbers are dev-set numbers, tuned and measured on the same 3 cases, and are
  harness validation only.

## 11. Judge calibration permits unlimited tuning against the only validation data

- **Severity:** major
- **Section:** §6.5, D-031, R8
- **Problem:** The bar is "exact agreement ≥ 90% **or** Cohen's κ ≥ 0.8", and the
  consequence of failing is "the judge prompt is revised and the set re-run before
  any accuracy figure is quoted". There is exactly one labelled set (~40 pairs),
  there is no held-out half, and there is no limit on revision rounds — so the
  procedure as written is "iterate the judge prompt until it passes on the 40 pairs
  you are grading it with". That is fitting the judge to its own validation set, and
  it is the same failure mode §6.1 exists to prevent one level up. R8 then records
  the judge as validated.
  Two further gaps: (i) **who labels** is unstated. Q2-07's option text said "you
  hand-label 40 pairs" (the user), but §6.5 says only "hand-labelled". If the
  implementing agent both constructs and labels the pairs, an LLM is grading an LLM
  against labels produced with the same priors, and the exercise is circular.
  (ii) The bar is a **disjunction**: the union of two thresholds is weaker than
  either, and with a skewed label distribution (most pairs obviously `exact` or
  obviously `wrong`) 90% raw agreement is attainable by a judge that fails on
  exactly the `synonym`/`broader`/`narrower` boundary that Q-22 says will be "doing
  real work".
- **Fix / Question:** Split the 40 pairs into a ~20-pair tuning half and a ~20-pair
  sealed half, fixed before any judge prompt is written; allow prompt revision only
  against the tuning half; quote the bar on the sealed half and allow it to be
  measured **once**. Make the bar a conjunction (agreement ≥ 90% **and** κ ≥ 0.8),
  or state the bar on the hard subset only. State explicitly that the user labels,
  and that the label set is committed before the judge prompt is finalised.

## 12. `dx_tokens_in_results` reports a count I cannot reproduce, because its rule is undefined

- **Severity:** minor
- **Section:** §3.2, §6.3
- **Problem:** §3.2: "A secondary metric `dx_tokens_in_results` (all significant
  diagnosis tokens present, full string absent) is also reported; **5** further
  cases fire it (13, 34, 123, 182, 208)." "Significant" is not defined, and the
  count is entirely a function of that definition. I reproduced it over the real
  file: with no stopword list, or with a stopword list of function words, or with a
  minimum token length of 1–4, the answer is **4** — {13, 34, 123, 208}. **182 fires
  only** if single-character tokens are dropped *and* the word "acute" is treated as
  insignificant. Case 182 is `Acute Hepatitis B`; "acute" appears nowhere in its
  `Test_Results` or `Physical_Examination_Findings`, and neither does a standalone
  "B" — so calling it a complete reveal requires discarding both the acuity and the
  serotype, i.e. the two tokens that make the diagnosis specific. Conversely a
  slightly more aggressive stopword list starts producing false positives: dropping
  "syndrome" makes case 31 (`Respiratory Distress Syndrome`) fire against its own
  `Objective_for_Doctor`, which reads "a newborn presenting with respiratory
  distress" — not a leak at all.
  §7's Phase 1 acceptance pins `dx_in_results == 29` (correct, verified) but pins
  nothing for this metric, so the wrong number will ship in `report.md`.
- **Fix / Question:** Either specify the rule exactly (token set, stopword list,
  minimum length, word-boundary vs substring) and pin the resulting count in a test
  the way `dx_in_results` is pinned — my reading of "all tokens, word-boundary,
  nothing dropped" gives **4**, not 5 — or drop the metric. It is a secondary
  reported figure whose value cannot currently be checked by anyone.

## 13. "A request naming a leaf matches its parent" has no tie-break, and the ambiguity is clinically real

- **Severity:** minor
- **Section:** §4.2
- **Problem:** §4.2's worked example assumes leaf names are unique within a case
  ("Ordering `hemoglobin` matches the same parent via its leaf"). Verified against
  the file: in **58 of 214** cases a leaf name appears under more than one top-level
  key, and in **9 of those** the duplicated leaf is a real orderable analyte, not a
  container word — e.g. case 10, 38, 55, 80, 101: `WBC` under both
  `Complete_Blood_Count` **and** `Urinalysis`; case 29: `Na` and `Glu` under both
  `Serum_Laboratory_Analysis` and `Urinalysis`; case 58: `Bilirubin` under
  `Blood_Tests` and `Urine_Analysis`; case 66: `Glucose` under `Laboratory_Studies`
  and `Urine_Tests`; case 30: `WBC` under `Complete_Blood_Count` and
  `Joint_Aspiration_Left_Wrist`. A doctor asking for "white count" gets either a
  CBC or a urinalysis (or, in case 30, a joint aspirate) depending on dict iteration
  order — different information, a different charge, and a different `match_tier`.
  The remaining 49 cases are the generic-container case (`Findings` under two
  imaging modalities), which the same rule would resolve arbitrarily.
- **Fix / Question:** State the tie-break in §4.2: on an ambiguous leaf, return
  **all** matching parents and charge once (defensible: the doctor asked for an
  analyte, not a panel), or return none and log an `unlisted_test`, or require the
  LLM tier to disambiguate using the parent key names it already sees. Add case 10
  (`WBC`) to the Phase 2 gatekeeper test list beside the existing synonym and
  leaf-matches-parent cases.

## 14. Three defects in how the (otherwise correct) split algorithm is written down

- **Severity:** minor
- **Section:** §6.1
- **Problem:** I traced the algorithm by hand against the real file and it produces
  **exactly dev = 40, heldout = 174**, with the dev membership D-029 depends on. The
  logic is right. Three things about the way it is *written* will bite:
  1. The comment on the defining line is wrong: `groups = {key: [case_ids]}
     # 35 groups over 72 cases`. There are **177** groups over 214 cases; 35/72 is
     the count of *multi-case* groups. An engineer reading the comment as a
     specification builds `groups` from duplicates only and produces a completely
     different, and permanently committed, split.
  2. There is no assertion that `len(dev) == 40`. The greedy has no `break`, so if
     the group-size distribution ever changed it would silently under-fill and
     `splits.json` would be written with, say, 39 dev cases.
  3. The test "asserts regeneration from this spec reproduces the committed file
     **byte-for-byte**", but the spec does not define the file: whether `dev` is
     stored in shuffled-group order (as `dev += groups[k]` produces) or sorted,
     JSON key order, or indentation. A byte-for-byte test needs the bytes specified.
- **Fix / Question:** Correct the comment to "177 groups; 35 of them hold 2–3 cases
  (72 cases total)"; add `assert len(dev) == 40` before writing; and specify the
  serialisation (recommend `json.dumps(..., sort_keys=True, indent=2)` with both id
  lists sorted).

## 15. Residual internal contradictions in the redesigned sections

- **Severity:** minor
- **Section:** §3.3 item 4, §2.3 diagram, §2.3 schemas, §7 Phase 1/2
- **Problem:** Four small ones, each in text revision 2 rewrote:
  1. **§3.3 item 4** says the interactive `MemorySaver` "holds the same
     **ground-truth-free** state". That is the exact formulation §1.1 was corrected
     away from as false: for the 29 flagged cases the checkpointed `encounter_log`
     contains the diagnosis string. Brief §11 requires a test over checkpoints, and
     this sentence is what an engineer will scope it from.
  2. **§2.3's diagram** routes `route -->|3 attempts invalid| fin`, bypassing
     `challenger_final`, while the prose two paragraphs below says `challenger_final`
     runs "before **every** finalize including forced stops". Decide which (skipping
     it on a parse failure is the sensible choice — say so).
  3. **The cost-steward cannot see any cost.** `test_cost_usd` is explicitly
     excluded from the subgraph schemas ("never cross the boundary"), and `spend_usd`
     (API cost) is irrelevant to test ordering. The node whose sole purpose is to
     object to expensive tests has no budget state at all. Either admit it into the
     `input_schema` or state that the steward reasons only from
     `config/test_costs.yaml` and the proposed test name.
  4. **Lists in `Physical_Examination_Findings`.** §4.2's fix correctly requires
     `str | dict | list` handling, but the Phase 1 acceptance criterion enumerates
     only `Test_Results` irregularities ("16 mixed-type + 2 list + 4 empty"). Two PEF
     cases contain lists — **37 and 103** — and `request_exam` walks PEF. Add them to
     the Phase 1/2 acceptance lists so the exam path is exercised too.

## 16. Accepted round-1 fixes that reference a mode the plan never defines

- **Severity:** minor
- **Section:** §6.4, D-030, §7, brief §7
- **Problem:** Round-1 #24.4 was accepted with "a `--no-report --cache` iteration
  mode is added so development does not burn free-tier requests". It appears
  **nowhere** in PLAN.md rev 2 — the only trace is "Run metadata: cache state
  (Q-34)" in §6.4. Meanwhile **D-030's provider rule depends on it**: pinned
  provider "for any run that writes a report; fallbacks permitted for `--cache` /
  `--no-report` development runs". So a recorded decision is conditioned on a CLI
  mode the plan does not contain, and Q-34's hard assertion (cache disabled for any
  run that writes a report) still means every iteration on the only 3 cases that
  exist burns free-tier requests — the problem #24.4 was accepted to fix.
  Two smaller omissions in the same class: brief §7 requires "a CLI trace viewer
  using `rich`" and Q-32 confirms it; no phase in §7 lists it as a task. And D-030's
  stated benefit for dev runs ("development stays resilient") is void by D-030's own
  finding that the model has exactly **one** endpoint — `allow_fallbacks: true`
  falls back among providers of the same model, and there are none.
- **Fix / Question:** Add the `--no-report --cache` iteration mode to §5/§6.4 with
  its semantics (no `report.md`, no `results.csv`, cache permitted, fallbacks
  permitted, recorded in metadata) or remove the dependency from D-030 and say that
  dev runs behave identically to reported runs. Add the `rich` trace viewer to a
  phase task list.

## 17. Two unstated consequences of decisions that are otherwise correct

- **Severity:** minor
- **Section:** §5 item 10, §3.3 item 8, §6.2, §6.4
- **Problem:**
  1. **Crash tracebacks have no stated destination.** §5 item 10: the runner
     "records outcome `crash` **with the traceback**". §3.3 item 8 asserts trace
     separation — judge traces go to `judge.jsonl`, never `traces/<case_id>.jsonl`.
     But a judge call is made per case by the same runner, and an exception from it
     (an HTTP error from `AsyncAnthropic` typically carries the request or response
     body) contains the `JudgeView` prompt, i.e. `Correct_Diagnosis` and
     `Management_and_Follow_Up`. If the runner writes the traceback into the
     per-case trace file or into `results.csv`, ground truth lands in a doctor-side
     artifact, breaking the plan's own invariant. Nothing is re-fed into a prompt
     (§5 item 7), so this is not a live leak to an agent — but it is an invariant
     the plan states and the plan's own crash rule can violate.
  2. **The 3-case set is one-third leak by construction.** The §6.2 rule picks the
     lowest-`case_id` `dx_in_results` case first; I traced it and the set is
     {2, 9, 12} with case 2 flagged. So the "leak-free subset accuracy" §6.4
     promises beside every headline number is computed over **2 cases**, and the
     all-cases figure is 33% contaminated against a 13.6% (29/214) base rate. This
     is a defensible choice (it exercises the flag path end to end) but the plan
     states neither consequence, and §6.4 promises a breakdown that is a 2-case
     denominator.
- **Fix / Question:** State in §5 item 10 that judge exceptions are recorded in
  `judge.jsonl` and that tracebacks are never written to `traces/<case_id>.jsonl` or
  `results.csv` (record an exception *type* and message there instead). Add one line
  to §6.2: the selection yields `medqa-0002` (flagged), `medqa-0009`, `medqa-0012`;
  the leak-free breakdown is therefore over n=2 and is reported as such.

---

## Summary

| | Count |
|---|---|
| Part B blockers | **3** (B-1, B-2, B-3) |
| Part B majors | **8** (B-4 … B-11) |
| Part B minors | **6** (B-12 … B-17) |
| Round-1 findings not fully resolved | **11 of 24** (#1, 6, 8, 10, 12, 13, 14, 15, 18, 21, 24) |

The single most important item is **B-1**: `challenged_this_finalize` cannot
express the condition `redec` is specified to test, because `challenger_final`
destroys the value `redec` needs before `redec` runs. The most likely
implementation of the text as written makes the `redec → PANEL` edge dead, which
deletes D-025's "exactly one re-decision" — the entire substantive difference
between the panel and the single doctor on the finalize path — while the Phase 4
acceptance test still passes. B-2 (`encounter_log` duplicating exponentially
across the subgraph boundary) is close behind, because it corrupts the traces,
the summary, `red_flag_turn`, and the amount of leaked gatekeeper text reaching
the doctor, all silently.

---

# Resolutions (Step 5, round 2)

Appended 2026-09-15. **All 17 Part B findings and all 11 partially-resolved
round-1 findings are accepted.** Four required user decisions, now recorded as
D-034 … D-037. Applied in `PLAN.md` **revision 3**.

**Independent verification.** Every disputed claim was re-derived against the
dataset. All three corrections to revision 2 hold:

| Claim | Rev 2 said | Verified |
|---|---|---|
| `dx_tokens_in_results` | 5, incl. case 182 | **4** — {13, 34, 123, 208}, under every token rule tried (minlen 1–4, with and without stopwords) |
| Split `groups` comment | "35 groups over 72 cases" | **177** groups total; 35 are multi-case, covering 72 |
| Leaf-name uniqueness | assumed | **58/214** cases have a leaf under >1 top-level key; **9** are real analytes |
| PEF containing lists | unmentioned | cases **37, 103** |

## Part A items (11 not fully resolved) — all accepted
#1 → B-1 fix. #6 → B-9. #8 → B-12. #10 → hypothesis moved to the parent in both
graphs, so `turn` counts the same thing in each. #12, #14 → B-4, B-5. #13 → B-6
wording. #15 → B-8. #18 → B-13/D-035. #21 → B-11/D-036. #24.2, #24.4, #24.5 →
B-15.2, B-16, B-2.

## Part B
- **B-1 blocker — accepted.** `redec` is removed. `route_action` now routes
  `finalize` to `challenger_final` when `challenged_this_finalize` is `False` and
  straight to `finalize` when `True`; `challenger_final`'s only edge returns to
  the panel. Forced stops use a **separate `challenger_stop` node** whose only
  edge goes to `finalize`, so Q-16's "before every finalize" holds without a
  re-decision when a cap has fired. The Phase 4 test now asserts the re-decision
  **happens once**, not merely that the path terminates.
- **B-2 blocker — accepted.** `encounter_log` is removed from the subgraph's
  `output_schema`; the subgraph returns `panel_events` (last-write, this
  deliberation only) and a parent-side `absorb_panel` node converts it into the
  `operator.add` delta. Test added: three panel turns, `len(encounter_log)` equals
  events actually emitted.
- **B-3 blocker — accepted.** `should_continue` becomes a **node** (`check_stop`)
  that writes `stop_reason`, with a separate routing function reading it.
  `finalize` writes `"finalize"` only when `stop_reason is None`. `crash` is
  dropped from `StopReason` and recorded as a runner-level outcome.
- **B-4 major — accepted.** A `@budget_guarded` decorator wraps **every** LLM
  node, converting `BudgetExceeded` into a state update setting
  `budget_exhausted`. `finalize` under a breach is constructed in Python from the
  existing differential with no model call; both challenger nodes are skipped.
- **B-5 major — accepted.** `spend_usd`, `test_cost_usd` and `parse_failures` get
  `operator.add` reducers and every node returns its own delta. §2.1 gains a
  value-semantics column.
- **B-6 major — accepted.** `turns_since_challenge` is deleted; the schedule is
  `turn % 3 == 0 and turn > 0`.
- **B-7 major — accepted.** `hypothesis` moves to the **parent** graph in both
  `single_doctor` and the panel; the panel subgraph holds only challenger,
  orchestrator and cost-steward, and its `input_schema` **excludes**
  `encounter_log`. Isolation is then structural, not prompt-level.
- **B-8 major — accepted.** `make_action_type(enabled)` builds the `Literal` and
  `OrchestratorDecision` per run; `route_action` gains an explicit branch for a
  valid-but-disabled action that records a `parse_failure` and re-prompts rather
  than raising. Structured-output fallback is a **per-run** decision recorded in
  metadata.
- **B-9 major — accepted.** Coverage denominator, the `n_scored ∈ {0,1}` printed
  value, bootstrap pairing and the matched-coverage caveat are all specified.
- **B-10 major — accepted** → D-034 (fuzzy tier dropped), D-037 (cap 200), plus
  the §6.2 statement that all Phase 3–5 numbers are dev numbers tuned and
  measured on the same three cases.
- **B-11 major — accepted** → D-036.
- **B-12 minor — accepted.** Rule specified (all tokens, word-boundary, nothing
  dropped); count corrected to **4** and pinned in a test like `dx_in_results`.
- **B-13 minor — accepted** → D-035.
- **B-14 minor — accepted.** Comment corrected to 177 groups; `assert len(dev) == 40`;
  serialisation specified as `json.dumps(sort_keys=True, indent=2)` with sorted lists.
- **B-15 minor — accepted** (all four).
- **B-16 minor — accepted.** `--no-report --cache` mode defined; `rich` trace
  viewer added to Phase 1; D-030's dev-fallback benefit noted as void.
- **B-17 minor — accepted.** Judge exceptions go to `judge.jsonl` only; the
  3-case set is named (`medqa-0002/0009/0012`) with the n=2 leak-free consequence.
