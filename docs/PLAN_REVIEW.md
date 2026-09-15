# Adversarial review of docs/PLAN.md
Round 1 — 2026-09-15 — fresh-context review

Scope: `docs/PLAN.md`, `docs/DECISIONS.md`, `docs/BRIEF.md`, `docs/DECISIONS_OPEN.md`
(context only), and read-only verification against
`dataset/agentclinic_medqa_extended.jsonl` + `dataset/MANIFEST.json`.
No `src/`, `scripts/`, `tests/` or `PHASE_0_NOTES.md` was read.

Dataset claims I verified and **confirmed** (so they are not findings): 214
non-blank lines, last line without trailing newline; single top-level key
`OSCE_Examination`; `Management_and_Follow_Up` on exactly one case (line 133);
4 cases with `Test_Results == {}` (69, 106, 111, 209); 1 case missing
`Past_Medical_History` (120); 1 case with `Symptoms == {}` (132); 9 cases with
undocumented `Patient_Actor` keys (18, 21, 26, 43, 77, 98, 132, 179, 191);
234 distinct top-level `Test_Results` keys with 165 singletons; `Vital_Signs`
present in 212/214; 31 `Correct_Diagnosis` strings repeating across 63 cases;
`Correct_Diagnosis` never appears verbatim in `Objective_for_Doctor`,
`Patient_Actor`, or `Management_and_Follow_Up`.

Claims I verified and found **wrong or under-specified** appear as findings
below (#8, #9, #19).

---

## 1. The panel's `challenger_final` → reopen path is an unbounded loop with no stop condition on it

- **Severity:** blocker
- **Section:** §2.3 (`encounter` + `doctor_panel` diagram), §5 item 4
- **Problem:** In the panel graph, `should_continue` — the node that enforces
  the turn cap and the spend cap — sits **only** on the edges leaving
  `ask_patient / request_exam / order_test / search_literature`. The finalize
  path is `PANEL → route{finalize} → challenger_final`, and
  `challenger_final -->|reopened| PANEL`. That cycle
  (`PANEL → route → challenger_final → PANEL → …`) never touches
  `should_continue`, never executes an action node, and therefore never
  increments a turn or evaluates any stop condition. If
  `ChallengerOpinion.should_reopen` keeps returning `true` and the orchestrator
  keeps choosing `finalize`, the encounter spins until the recursion limit
  (140) aborts the graph — burning roughly 3–4 LLM calls per spin, i.e. up to
  ~140 wasted requests against a 1000/day free-tier allowance (D-023), and
  producing **no `FinalAnswer`**, which contradicts §5 item 4 ("all four produce
  a final answer").
  §2.3 says the challenger "may reopen the encounter **once**", but
  `EncounterState` (§2.1) has **no key that records whether a reopen has
  happened** — `turns_since_challenge` does not encode it. The "once" is
  therefore enforced by nothing.
- **Fix / Question:** Add `reopen_count: int` (or `reopened: bool`) to
  `EncounterState`; make `challenger_final` route to `finalize`
  unconditionally when `reopen_count >= 1`; and put `should_continue` (or an
  equivalent cap check) on the `challenger_final → PANEL` edge as well, so the
  turn and spend caps are evaluated on every path back into the panel. Also
  state explicitly which node increments `turn`.

## 2. §1.1's central structural claim is false as written, and the test that is supposed to prove it cannot

- **Severity:** blocker
- **Section:** §1.1, §3.1 item 3, §3.2
- **Problem:** §1.1 asserts: "`EncounterState` has **no field capable of
  holding ground truth**. That is the property the leakage tests assert
  structurally, rather than by scanning strings." This is not true.
  `encounter_log: Annotated[list[Event], add]` and `summary:
  EncounterSummary` are free-text channels, and §3.2 itself concedes that
  gatekeeper output — which lands in `encounter_log` — contains the exact
  diagnosis string in 28 cases (see #8). So `EncounterState` demonstrably *is*
  capable of holding ground truth, and does.
  §3.1 item 3 then says the property is "asserted by a test over the
  TypedDict's annotations". A test over annotations can only check *field
  names and types*; it cannot distinguish `encounter_log` (allowed to carry
  gatekeeper text) from a hypothetical `correct_diagnosis: str`. The plan's
  headline guarantee therefore rests on an enforcement mechanism that is not
  specified and, as described, cannot work. Two engineers will implement this
  completely differently: one writes a name/type allow-list (which passes
  trivially and proves nothing), the other writes a string scan (which fails on
  28 cases).
- **Fix / Question:** Restate the property precisely and testably. The true,
  provable property is: *no `EncounterState` key is ever written from
  `Case.Correct_Diagnosis` or `Case.Management_and_Follow_Up`, because no
  doctor-side view exposes those fields.* Enforce it as (a) a frozen-dataclass
  view test asserting `DoctorView`/`PatientView`/`GatekeeperView` have no
  attribute derived from those two fields, plus (b) an explicit allow-list test
  naming every `EncounterState` key and its permitted source view, which fails
  when a new key is added. Then state separately that string-scanning leakage
  tests run only over the leak-free subset (see #9).

## 3. The challenger's reopen authority and the cost-steward's veto/downgrade power are choices with no decision behind them — and the schemas cannot express them

- **Severity:** blocker
- **Section:** §2.3, §4 (`ChallengerOpinion`, `CostStewardOpinion`)
- **Problem:** The brief §5.3 defines the challenger as a node that "argues
  against the leading diagnosis and names the most dangerous alternative not
  yet ruled out" and the cost-steward as one that "objects to tests unlikely to
  change management". Neither is given authority over control flow. `Q-16`
  decides only *when* they run, not *what power they have*. The plan grants
  the challenger the power to reopen a finished encounter
  (`should_reopen: bool`) and the cost-steward the power to "downgrade
  `order_test` to a different action" — a **veto**. Neither traces to
  `DECISIONS.md` or to the brief.
  Worse, `CostStewardOpinion` has only `objection: str | None` and
  `would_change_management: bool`. There is **no field naming the replacement
  action**, so the schema physically cannot express the "downgrade" §2.3
  describes. It is also undefined whether `would_change_management == false`
  *is* the veto, or whether the objection is merely advisory and the
  orchestrator re-decides (which would require a second orchestrator call and a
  loop the diagram does not show).
  Related concrete defect in the same subgraph: `pveto{action == order_test?}`
  routes `no` straight to `pout` (subgraph END). On that path **nothing ever
  copies `proposed_action` into `action`**, yet `route_action` (§2.2/§2.3)
  dispatches on the executed action. As drawn, every non-`order_test` turn
  reaches `route_action` with a stale or `None` `action`.
- **Fix / Question:** Put to the user: *"Should the challenger be able to
  reopen a finalized encounter, and should the cost-steward be able to veto or
  replace a test order — or are both advisory nodes whose opinions are written
  into `encounter_log` for the orchestrator to weigh on its next decision?"*
  Whichever is chosen, add the missing schema field (e.g.
  `replacement_action: Action | None`), define veto semantics explicitly, and
  add an explicit `promote_action` step (or a single `action` key with no
  `proposed_action`) so the no-veto path is well defined.

## 4. The brief's "Test-selection" sub-role has been silently merged into the orchestrator

- **Severity:** blocker
- **Section:** §2.2, §2.3, §4 (`OrchestratorDecision`), §7 Phase 4
- **Problem:** Brief §5.3 lists **four sub-role nodes** — Hypothesis,
  Test-selection, Challenger, Cost-steward — *and separately* an "Orchestrator
  node + conditional edge". The plan has no `test_selection` node; its job
  ("proposes the most informative next action and what result would change the
  differential") has been folded into `OrchestratorDecision.expected_information`,
  and the plan even cites "(§5.3)" next to that field as though this were the
  brief's instruction. No entry in `DECISIONS.md` authorises the merge.
  This is not cosmetic: it changes how many LLM calls a panel turn costs, it
  changes what "four sub-roles" in the Phase 4 acceptance criterion means, and
  it directly contradicts §1.2's description of `single_doctor` as having
  "one doctor node, no sub-roles" — the `single_doctor` diagram in §2.2
  contains `brief_doctor`, `orchestrator` **and** `hypothesis`, i.e. two of the
  brief's four sub-roles. The panel-vs-single comparison therefore isolates
  only challenger + cost-steward, which is a defensible design but is *not*
  what §1.2 says it is.
- **Fix / Question:** Put to the user: *"The brief lists Test-selection as a
  separate sub-role. Option (a): add a `test_selection` node to the panel and
  keep the orchestrator as a pure router — panel turns cost one more call;
  `single_doctor` keeps orchestrator+hypothesis. Option (b): record a decision
  that Test-selection is merged into the orchestrator, and correct §1.2 to say
  `single_doctor` has orchestrator+hypothesis+finalize, so the comparison is
  explicitly 'panel adds challenger and cost-steward'. Which?"*

## 5. Rule-based red-flag detection is undefined, and depending on where it runs it leaks unordered exam findings into doctor-visible state

- **Severity:** blocker
- **Section:** §5 item 6, §3 visibility matrix
- **Problem:** Q-28 chose "model-reported **plus** a rule-based check on
  `Vital_Signs`", and Q-28's own option text admits this "requires defining
  thresholds". **No thresholds were ever decided**, yet §8 declares the open
  questions list empty. Three separate gaps:
  1. **Thresholds undecided.** Verified: `Vital_Signs` is a dict of free text
     with 16 distinct sub-keys and no consistent format — `'125/80 mmHg'`,
     `'Within normal range'`, `'Normothermic'`, `'Normal for age'`,
     `'120 bpm (normal for age)'`, `'90/min'` vs `'90 bpm'`, `'36.6°C (97.9°F)'`
     vs `'98.5°F (36.9°C)'`. Case 3 is a child with HR 120 explicitly annotated
     "normal for age"; any naive adult tachycardia threshold flags it. A
     parser plus age-aware thresholds is a real clinical-rule design task the
     plan does not contain.
  2. **Where it runs is undefined.** Interpretation A: a node inside the
     encounter graph. Then it must read `Physical_Examination_Findings`, which
     the §3 visibility matrix grants to **no node except `request_exam`**, and
     the resulting flag is written into `EncounterState.red_flags` — which is
     doctor-visible. That hands the doctor vital-sign information it never
     ordered, violating brief §5.2 ("Never reveals unordered results") and
     contradicting the plan's own visibility matrix. Interpretation B: the
     eval runner computes it offline over gatekeeper results already returned.
     No leak, but then it measures a different thing. The plan does not say
     which, and the visibility matrix has no row for it.
  3. **`red_flag_turn` is unrecoverable** — see #14.
- **Fix / Question:** Put to the user: *"(a) Where should the rule-based
  red-flag check run — inside the encounter (needs a new visibility-matrix row
  and leaks vitals the doctor did not order), or offline in the eval runner
  over results the gatekeeper already returned? (b) What are the thresholds,
  given `Vital_Signs` is free text in 16 formats including 'Within normal
  range' and 'normal for age' paediatric values?"* Until answered, this is an
  open question and §8 is wrong.

## 6. How an abstention is scored is undecided, and §8 claims there are no open questions

- **Severity:** blocker
- **Section:** §4 (Q-15 handling), §6.3, §8
- **Problem:** `FinalAnswer.abstain` exists; §6.3 reports `abstained` and
  `parse_failure` as separate columns; Q-15 says a parse failure forces
  `abstain=true` and must never be merged with clinical abstention. But
  **nothing anywhere says how either is scored in the headline accuracy.**
  Three defensible readings, all producing different numbers: (a) abstention
  counts as incorrect; (b) abstention is excluded from the denominator (as
  Q-08 does for API errors) and reported as coverage; (c) clinical abstention
  is excluded but parse-failure abstention counts as incorrect. Brief §9.3
  ("report accuracy vs coverage") implies (b) for the *experiment*, but the
  Phase 3 headline number needs a rule now, and at n=3 a single abstention
  moves accuracy by 33 points.
  Also unspecified: what `FinalAnswer.diagnosis` contains when
  `abstain == true` (the field is a required `str`), and what the judge is
  asked to do with it — a judge handed an empty string or "unable to
  determine" will produce `match_type: "wrong"` and `correct: false` unless
  the runner short-circuits it.
- **Fix / Question:** Put to the user: *"How are abstentions scored in headline
  accuracy — incorrect, or excluded from the denominator with coverage reported
  separately? And separately for parse-failure abstentions? What goes in
  `FinalAnswer.diagnosis` when abstaining, and is the judge called at all?"*

## 7. The 3-case evaluation subset may be impossible to construct as specified

- **Severity:** blocker
- **Section:** §6.1, §6.2 (D-022, D-024)
- **Problem:** D-022 requires the 3 evaluation cases to be drawn from the dev
  split and to include "at least one `dx_in_results` case (D-015) **and** one
  empty-`Test_Results` case (D-018)". Verified: there are exactly **four**
  empty-`Test_Results` cases — lines 69, 106, 111, 209 — and 111 and 209 share
  the diagnosis `Central retinal artery occlusion`, so under D-024's
  no-diagnosis-spans-both-splits rule they form **three groups covering four
  cases out of 214**. A dev split of 40 drawn from 214 contains none of those
  four cases with probability ≈ 0.43. So there is roughly a **43% chance the
  constraint is unsatisfiable**, and the plan specifies no fallback. It also
  specifies no rule for *which* 3 cases are drawn ("drawn deterministically
  from dev" is not an algorithm), and once `splits.json` is committed (D-006,
  D-024: "Never regenerated") the failure is permanent.
  Secondary consequence: with 3 cases of which ≥1 is a `dx_in_results` leak
  case, the "leak-free subset accuracy" that §6.4 promises is computed over
  ≤2 cases.
- **Fix / Question:** Generate `splits.json` **and then check** that dev
  contains at least one empty-`Test_Results` case and at least one
  `dx_in_results` case before committing; if it does not, put to the user:
  *"The seeded dev split contains no empty-`Test_Results` case. Options: (a)
  pick a different seed and record it as a superseding decision; (b) drop the
  empty-`Test_Results` requirement from the 3-case subset and cover it with a
  unit test instead; (c) enlarge dev. Which?"* Also specify the deterministic
  3-case selection rule explicitly (e.g. "sorted by case_id after filtering,
  take the first satisfying assignment").

## 8. `dx_in_results` is computed by an unspecified matching rule; the plan's "27 / 187" numbers are verifiably wrong under any reasonable rule

- **Severity:** major
- **Section:** §3.2, §6.3, §6.4 (D-015)
- **Problem:** I reproduced the count four ways against the real file:

  | Matching rule | Count |
  |---|---|
  | Case-sensitive substring, **values only** | 27 ← the plan's number |
  | Case-insensitive substring, values only | 27 |
  | Case-insensitive substring, **keys + values** | **28** |
  | As above, with trailing parenthetical stripped from the diagnosis | **29** |

  The plan's 27 is only reproducible if the match ignores dictionary **key
  names**. Concrete counter-example — case 154, `Correct_Diagnosis` =
  `"Varicella"`, `Test_Results` keys include `Varicella_Specific_Tests` and the
  nested key `IgM_Antibodies_to_Varicella_Zoster_Virus`. The gatekeeper returns
  matched *entries*, i.e. key **and** value, so the doctor is handed the word
  "Varicella" — yet case 154 is scored as leak-free.
  Second counter-example — case 104, `Correct_Diagnosis` =
  `"Legg-Calvé-Perthes disease (LCPD)"`, `Test_Results` value reads
  `"…consistent with Legg-Calvé-Perthes disease"`. An exact-string match misses
  it because of the `(LCPD)` suffix; the doctor still receives the complete
  diagnosis. (Q-22 already notes that `Correct_Diagnosis` "sometimes appends
  abbreviations like (PML)", so this is a known pattern, not a one-off.)
  Consequently §3.2's "27 of 214" and the implied leak-free denominator of 187
  are both wrong, and the leak-free accuracy — a headline number in §6.4 — is
  computed over a contaminated subset.
  Minor factual slip in the same paragraph: §3.2 says the string is embedded in
  "`Test_Results` / `Physical_Examination_Findings`". Verified: **zero** cases
  have it in `Physical_Examination_Findings`; all are in `Test_Results`.
- **Fix / Question:** Specify the `dx_in_results` rule in the plan, in code, as
  a testable function: normalise case, strip a trailing `(...)` abbreviation
  from the diagnosis, and match over the **flattened keys and values** of both
  `Test_Results` and `Physical_Examination_Findings`. Update the counts to 29
  (or whatever the chosen rule yields) and record the leak-free denominator as
  a computed value, never a literal. Additionally, consider a second, looser
  flag `dx_tokens_in_results` (all significant diagnosis tokens present, full
  string absent) — I verified 5 further cases where this fires and the reveal is
  effectively complete: 13 `Posterior hip dislocation`, 34 `Coarctation of the
  aorta`, 123 `Pseudomonas keratitis`, 182 `Acute Hepatitis B`, 208
  `Femoropopliteal artery stenosis`.

## 9. The leakage test the brief mandates is unsatisfiable on the leak cases, and the plan does not say how the test selects cases

- **Severity:** major
- **Section:** §3.2, §7 Phase 1 acceptance
- **Problem:** Brief §11 requires a test asserting that `Correct_Diagnosis`
  (case-insensitive) "never appears in any doctor-side or evidence-side prompt,
  in any doctor-visible state channel, or in encounter-graph checkpoints, both
  at start and after a full scripted encounter." §3.2 correctly observes this
  cannot hold for the leak cases — but it only says so in prose and in "test
  docstrings". It never states which cases the string-scanning test actually
  runs over, and Phase 1's acceptance criterion says only "leakage tests pass
  on state schema and views". Two engineers will build different tests: one
  runs the string scan over all 214 (fails, and gets "fixed" by weakening the
  assertion), the other runs it only over leak-free cases (passes but is
  scoped by a flag whose correctness is #8's problem).
- **Fix / Question:** State explicitly in §3.2 and in the Phase 1 acceptance
  criteria: the string-scan leakage test is parameterised over
  `[c for c in cases if not c.dx_in_results]` and **asserts the negative**;
  a second test asserts the *expected* presence of the diagnosis in gatekeeper
  output for the flagged cases (so a future change to `dx_in_results` breaks
  loudly rather than silently); and a third, case-independent test asserts the
  view/state-source allow-list from #2.

## 10. Panel and `single_doctor` are structurally asymmetric in ways that contaminate the project's headline comparison

- **Severity:** major
- **Section:** §2.2 vs §2.3
- **Problem:** Three differences that are not "panel has extra sub-roles":
  1. **Node ordering.** `single_doctor` is `action → hypothesis → should_continue
     → orchestrator`; the panel is `hypothesis → orchestrator → action →
     should_continue → hypothesis`. On a forced stop the panel goes
     `action → should_continue{stop} → finalize`, so **the last action's result
     never passes through `hypothesis` before the panel finalizes**, whereas in
     `single_doctor` it always does. The panel is systematically handicapped on
     exactly the cases (forced stops) where the difference matters most.
  2. **Q-16 is violated on forced stops.** Q-16 says the challenger runs
     "before **every** `finalize`". `cont -->|stop| fin` bypasses
     `challenger_final` entirely, so turn-cap and spend-cap finalizations get no
     challenger. Given a 20-turn cap and R1's own concern about forced-stop
     rate, this is not a rare path.
  3. **Turn-1 hypothesis.** The panel's first node inside `PANEL` is
     `hypothesis`, executed before any information has been gathered, costing a
     call to produce a differential from `Objective_for_Doctor` alone.
     `single_doctor` has no equivalent.
- **Fix / Question:** Route `should_continue{stop}` through `challenger_final`
  (with reopen disabled on that path, per #1) and through a final `hypothesis`
  update, so both graphs incorporate the last result and run the challenger
  before every finalize. Or, if the asymmetry is intentional, record it as a
  decision and state in the report that panel-vs-single confounds sub-roles
  with loop ordering.

## 11. `Objective_for_Doctor` has nowhere to live in `EncounterState`, and the truncation rule may throw it away

- **Severity:** major
- **Section:** §2.1, §2.2 (`brief_doctor`), §5 item 7
- **Problem:** §2.2 says `brief_doctor` "seeds state from
  `DoctorView.objective_for_doctor`", and §3's matrix says every doctor node
  then reads it "via state". But `EncounterState` (§2.1) has **no key for it**:
  the candidates are `encounter_log` (append-only event list) and `summary`
  (`EncounterSummary`, whose Q-29 fields are `findings`, `tests_ordered`,
  `ruled_out`, `open_questions` — none of which is an objective). If the
  objective is written as the first `encounter_log` event, §5 item 7's rule —
  "capped at ~2000 characters, **oldest entries dropped first**" — will
  eventually discard the doctor's brief, silently, mid-encounter.
  Related: `EncounterSummary`, `Event`, `RedFlag` and `StopReason` are
  referenced in §2.1 but never defined in §4's schema list, even though §4 is
  the section the brief (Step 3 item 3) requires to contain "JSON schemas for
  all structured outputs" and Q-29's summary shape is load-bearing for context
  management.
- **Fix / Question:** Add an explicit `objective: str` key to `EncounterState`
  (never truncated), and define `EncounterSummary`, `Event`, `RedFlag`,
  `StopReason` in §4. Also state which node produces `EncounterSummary.findings`
  and `tests_ordered` — `HypothesisUpdate` (§4) emits `differential`,
  `ruled_out`, `open_questions` and `red_flags`, so two of the four Q-29 summary
  fields currently have **no producer**.

## 12. The spend cap cannot fire with free models, its unit is ambiguous, and its enforcement point cannot produce a `FinalAnswer`

- **Severity:** major
- **Section:** §5 item 4, §5.1, §2.1
- **Problem:** Three compounding issues:
  1. **It is inert.** D-020 puts every OpenRouter role on a `:free` model, so
     `spend_usd` is ~$0 for the entire run. The $0.50/case and $25/run caps
     (Q-25) — listed in §5 as one of only three real stop conditions — can
     never fire. The plan presents a three-condition stop design that is in
     practice a two-condition design (`finalize` or turn cap). The actual
     binding resource is the 1000 requests/day cap, which has no guard at all
     (see #13).
  2. **The unit is ambiguous.** `EncounterState` carries both `spend_usd` and
     `test_cost_usd`. §5's table row says only "Spend cap per case **$0.50**".
     Q-13 prices tests in "round USD" from an illustrative table. If an
     engineer checks `spend_usd + test_cost_usd` against $0.50, the first CBC
     terminates the encounter. If they check `spend_usd` only, the cap is inert
     (issue 1). Both readings are supportable from the text.
  3. **It cannot stop gracefully.** Q-25 says the guard is "checked *before*
     each LLM call using the running total". That check lives in the OpenRouter
     client, not in the graph, so on breach it can only raise — which aborts
     the graph with no `FinalAnswer`, contradicting §5 item 4. It also creates
     two independent spend totals (the client's counter and
     `EncounterState.spend_usd`) with no stated reconciliation.
- **Fix / Question:** State in §5.1 that the cap applies to `spend_usd` (real
  API cost) only, and that `test_cost_usd` is a simulated metric never compared
  against it. Make the client's budget breach raise a typed
  `BudgetExceeded`, caught by the orchestrator node, which sets
  `stop_reason="spend_cap"` and routes to `finalize` using the existing
  differential without a further LLM call. And put to the user: *"With free
  models the spend cap is inert — should we add a per-case and per-run
  **request** cap as the real guard?"*

## 13. No guard at all on the 1000 requests/day free-tier cap, and a daily-cap 429 silently shrinks the accuracy denominator

- **Severity:** major
- **Section:** §5.1, §9 R4 (D-023, Q-08)
- **Problem:** D-023 records two limits — 20 req/min **and 1000 req/day** — and
  the plan implements a guard for only the first (an 18/min token bucket). At
  18 req/min sustained, the daily allowance is exhausted in **56 minutes**.
  Rough order of magnitude for the intended work: brief §8 asks for
  single_doctor vs panel × with/without evidence = 4 configurations; at 3 cases
  × 20 turns × ~3–4 calls per panel turn that is ~1000–1400 requests for one
  full matrix — i.e. the daily cap is a live constraint, not a theoretical one,
  before any debugging re-runs.
  The failure mode is silent: a daily-cap 429 is indistinguishable at the HTTP
  layer from a per-minute 429, so Q-08's policy retries it three times over ~7s
  (the cap resets at a fixed daily boundary, so all three fail), then records
  the case as `error`. Q-08 excludes `error` from accuracy. At n=3, that turns
  a quota breach into a quietly reported accuracy over n=2 — or n=0.
  §6.4's report contents do not include an error count or the accuracy
  denominator.
- **Fix / Question:** Add a persistent daily request counter (a small JSON file
  keyed by UTC date) checked in the same place as the token bucket, with a
  pre-flight estimate that refuses a run projected to exceed the remaining
  daily allowance — the exact analogue of Q-25's pre-flight spend estimate.
  Inspect the 429 response body/headers to distinguish minute-cap from
  daily-cap and abort the whole run on the latter rather than degrading case by
  case. Add `n_scored`, `n_error`, `n_abstained` and the denominator to every
  accuracy figure in `report.md`.

## 14. Reducer choice makes `red_flag_turn` and the differential history unrecoverable

- **Severity:** major
- **Section:** §2.1, §5 item 6, §6.3
- **Problem:** §2.1 states that every key except `encounter_log` uses
  last-write-wins. `red_flags: list[RedFlag]` is therefore overwritten on every
  `hypothesis` update. §5 item 6 requires "First-flag turn is recorded" and
  §6.3 lists `red_flag_turn` as a per-case metric — but if the model raises a
  red flag on turn 3 and omits it from turn 4's `HypothesisUpdate`, the flag
  disappears from state and the first-flag turn is lost. The same applies to
  `differential`: `top_k` and `in_differential` are computed from the *final*
  differential only, which is fine, but any analysis of how the differential
  evolved is impossible.
  A second, smaller instance: `parse_failures: int` under last-write-wins is
  safe only while exactly one node writes it per super-step; the plan asserts
  that as a blanket property ("Only one node writes each key per super-step")
  without noting that `red_flags` is written by `hypothesis` and, under
  interpretation A of #5, also by a rule-based checker.
- **Fix / Question:** Derive `red_flag_turn` from `encounter_log` (append-only)
  rather than from `red_flags`, and say so in §5 item 6; or give `red_flags` an
  append-with-dedup reducer and document it. Either way, name in §2.1 the
  single writer of every last-write-wins key.

## 15. `search_literature` is a routable action in Phases 3 and 4 but the evidence agent does not exist until Phase 5

- **Severity:** major
- **Section:** §2.2, §2.3, §7 Phase 3/4/5, Q-18
- **Problem:** Both graph diagrams include `lit[search_literature]` as one of
  five routable actions from Phase 3 onward, and Phase 3's acceptance criterion
  is "routing tested for **every** action". The evidence agent is Phase 5. The
  plan never says what `search_literature` does in Phases 3–4. Two
  interpretations with different consequences: (a) the action is removed from
  the `Action` literal until Phase 5 — then the orchestrator's choice space
  differs between phases and Phase 3/4 results are not comparable with Phase 5
  results; (b) it routes to a stub returning "no results" — then the doctor
  burns turns against the 20-turn cap on a dead action, and the panel/single
  comparison is run against a graph that is not the final one.
  The same gap exists permanently, not just between phases: Q-18 says the
  evidence agent is "skipped entirely" if `NCBI_EMAIL` is unset, with no stated
  behaviour for the `search_literature` action in that case. And brief §8's
  configuration matrix explicitly includes "without the evidence agent" runs,
  which the plan never mentions.
- **Fix / Question:** State the rule in §4: `search_literature` is present in
  the `Action` enum only when the evidence agent is enabled for the run, the
  enabled/disabled state is recorded in run metadata and in `report.md`, and
  runs with different action spaces are never compared. Also add the 2×2
  configuration matrix from brief §8 to §6.

## 16. `top_k` in "pure Python" will disagree with the judge, making `top_1` and `judge_correct` mutually inconsistent

- **Severity:** major
- **Section:** §2.4, §6.3 (Q-22, Q-23)
- **Problem:** §2.4 says `match_diagnosis` calls `claude-opus-5` for free-text
  clinical equivalence, while `compute_top_k` is "pure Python over the
  differential". Top-k requires exactly the same equivalence judgement —
  deciding whether `"Malignant melanoma"` in position 1 matches
  `Correct_Diagnosis`. A pure-Python string comparison will systematically
  report `top_1 = false` on cases where the LLM judge reports
  `match_type = "synonym"` and `judge_correct = true`. Q-22 itself notes that
  `Correct_Diagnosis` "mixes sentence and title case and sometimes appends
  abbreviations like (PML), so `synonym` will be doing real work" — which is
  precisely the work pure Python will not do. The result is a report in which
  top-1 accuracy is lower than top-1-equivalent overall accuracy, which is
  incoherent.
- **Fix / Question:** Have the judge return a match verdict for **each**
  differential entry in one call (e.g. `entry_matches: list[bool]` alongside
  `match_type`), and compute top-k from that. Then `top_1 == judge_correct` by
  construction when the differential's first entry equals `diagnosis`. If the
  user prefers to keep it deterministic, state explicitly in §6.4 that top-k
  uses strict normalised string matching and is therefore a **lower bound** not
  comparable with `judge_correct`.

## 17. `JudgeVerdict.correct` and `match_type` can disagree, and the plan does not say which is authoritative

- **Severity:** major
- **Section:** §4 (`JudgeVerdict`), §6.3 (Q-22)
- **Problem:** The judge emits both `correct: bool` and `match_type:
  Literal["exact","synonym","broader","narrower","wrong"]`. Q-22 defines
  correctness *as a function of* `match_type` (exact + synonym = correct;
  broader/narrower = lenient only). So `correct` is redundant, and an LLM will
  sometimes emit `correct: true, match_type: "broader"`. One engineer will use
  `verdict.correct` for `judge_correct`; another will derive it from
  `match_type`. These give different headline numbers, and at n=3 a single
  disagreement is 33 points.
- **Fix / Question:** Remove `correct` from `JudgeVerdict` and derive
  `judge_correct = match_type in {"exact","synonym"}` and
  `lenient_correct = match_type != "wrong"` in code. If `correct` is retained
  for the judge's own sanity, add a runtime assertion that it agrees with the
  derived value and log every disagreement as a judge-quality signal.

## 18. Gatekeeper match granularity is undefined, and the natural implementation reveals unordered results

- **Severity:** major
- **Section:** §4 (gatekeeper matching), §3.1 item 6 (Q-11)
- **Problem:** Q-11's cascade matches "key names", and §3.1 item 6 promises
  "matched entries only — never the whole dict, never unordered results". But
  `Test_Results` is **not flat**: I verified 62 of 214 cases nest three levels
  deep (e.g. case 154: `Varicella_Specific_Tests →
  IgM_Antibodies_to_Varicella_Zoster_Virus → Findings`). The 234-key inventory
  counts **top-level** keys only. So "key names" is ambiguous:
  - **Interpretation A (top-level only):** a doctor who orders "hemoglobin"
    never matches, is told "Not available for this patient", and is charged
    (Q-12) — a fabricated unavailability. Conversely, a doctor who orders
    "CBC" receives the *entire* `Complete_Blood_Count` sub-dict including
    values they did not ask for, which is literally "the whole dict" that
    §3.1 item 6 forbids.
  - **Interpretation B (leaf-level):** matching happens over flattened paths,
    a request returns one leaf, and the LLM fallback sees hundreds of key
    paths rather than ~5.
  These produce materially different `unlisted_tests` rates, different test
  costs, and different amounts of information reaching the doctor.
  Related, verified: D-018 says "**2** with `Test_Results` mapping keys to
  plain strings". That is true for cases where *all* values are strings (150,
  187), but **14 further cases** have a *mix* of string and dict values at the
  top level (17, 67, 88, 114, 118, 120, 125, 140, 141, 150, …). A gatekeeper
  that assumes `dict[str, dict]` will fail on 16 cases, not 2.
- **Fix / Question:** Specify the matching granularity in §4 with a worked
  example, and state the return rule (e.g. "match at top level; return the
  matched key and its complete sub-tree; a request naming a leaf matches its
  parent"). Note explicitly that a top-level match returns the whole sub-tree
  and that this is the intended behaviour of "ordering a test panel", so §3.1
  item 6's wording does not contradict it. Extend the loader/gatekeeper to
  handle `str | dict | list` values at every level (16 cases, plus 4 list
  values I found at depth 2).

## 19. The split algorithm is not specified; "stratified" is used to mean its opposite; case-only duplicate diagnoses will span the splits

- **Severity:** major
- **Section:** §6.1 (Q-36, D-024)
- **Problem:** Three distinct problems in one sentence:
  1. **"Stratified" means the opposite of what is intended.** In standard
     usage, stratifying by a label means *every label appears in both splits in
     proportion*. What Q-36/D-024 want is the opposite: **grouping** by
     diagnosis so no diagnosis appears in both (`GroupShuffleSplit`, not
     `StratifiedShuffleSplit`). An engineer reaching for
     `sklearn.model_selection.StratifiedShuffleSplit` on the strength of the
     word "stratified" would produce exactly the leak the decision exists to
     prevent — and, since `splits.json` is committed once and never
     regenerated, permanently.
  2. **The algorithm is not determined by the seed alone.** `seed 20260915`
     plus "dev 40 / heldout 174" does not define a split: shuffling groups vs
     shuffling cases, `random.Random` vs `numpy.random`, the iteration order of
     the diagnosis→cases mapping, and the tie-breaking used to hit exactly 40
     all change the result. Two engineers produce different committed splits
     from the same stated spec.
  3. **Case-only duplicates will span the splits.** Verified: grouping on the
     raw string gives 31 repeated strings over 63 cases, but
     case-insensitively it is 35 strings over 72 cases. Five diagnoses differ
     only in capitalisation and would therefore be treated as distinct groups:
     `Neuroleptic malignant syndrome`/`Neuroleptic Malignant Syndrome`,
     `Hair tourniquet syndrome`/`Hair Tourniquet Syndrome`,
     `Cardiac Contusion`/`Cardiac contusion`,
     `Hypertrophic Cardiomyopathy`/`Hypertrophic cardiomyopathy`,
     `Lambert-Eaton Syndrome`/`Lambert-Eaton syndrome`. That is 10 cases that
     can land on both sides of the split — precisely the tuning leak Q-36 cites
     as its rationale.
- **Fix / Question:** Say "**grouped** by `Correct_Diagnosis` (no diagnosis
  appears in both splits)", not "stratified". Group on
  `" ".join(dx.strip().lower().split())`. Write the exact algorithm into §6.1
  (sort groups by normalised key → `random.Random(20260915).shuffle(groups)` →
  greedily fill dev to exactly 40) and add a test that regenerating from the
  spec reproduces the committed `splits.json` byte-for-byte.

## 20. Q-05's pinned-provider decision has no value, so §8's "no open questions" is false

- **Severity:** major
- **Section:** §4, §5.1, §6.4, §8 (Q-05, D-020)
- **Problem:** Q-05 decided "Providers pinned (`allow_fallbacks: false`) for
  reported runs" — the option text is `provider: {allow_fallbacks: false,
  order: [...]}`. **No `order` value was ever chosen**, and for a `:free` model
  ID the set of serving providers is exactly the thing that changes without
  notice (R3 acknowledges this for the model but not for the provider). With
  `allow_fallbacks: false` and a pinned order, the run fails outright the
  moment that provider is unavailable; with the field omitted, OpenRouter
  routes freely and the plan's reproducibility claim in §6.4 ("provider
  resolution per run") becomes after-the-fact detection, which is Q-05's
  *rejected* option (b).
  §8 states all 45 `[ASK]` items are resolved. This one, the red-flag
  thresholds (#5), the abstention scoring rule (#6), the judge calibration set
  (#21) and the 3-case selection rule (#7) are not.
- **Fix / Question:** Put to the user: *"Which upstream provider(s), in which
  order, should `nvidia/nemotron-3-super-120b-a12b:free` be pinned to? If the
  free endpoint has exactly one provider, is `allow_fallbacks: false` with no
  `order` acceptable, and should the run abort or fall back if that provider is
  down mid-run?"* Also correct §8 to list the remaining open items.

## 21. The judge is unvalidated, and the calibration that is supposed to validate it has no defined set

- **Severity:** major
- **Section:** §7 Phase 5, §9 R8 (Q-22, D-021, D-022)
- **Problem:** R8 acknowledges the judge is unvalidated until Phase 5, and
  Phase 5's acceptance criterion is "hand-labelling CLI produces an agreement
  report". But the plan never says **what is hand-labelled**: how many cases,
  drawn from where, labelled by whom, or what agreement statistic and threshold
  would count as validation. Under D-022 only **3 cases** are ever run, so
  there are 3 judge verdicts in existence — an agreement report over n=3 is not
  a validation, and the plan's own R1 logic applies with full force. There is
  also no stated consequence: if agreement is poor, what happens to the numbers
  already reported?
  Secondary, unhandled consequence of D-021: with `ant auth login` subscription
  auth there is no per-call billing figure, so R5's "judge cost tracked
  separately" has no cost to track — only token counts against an opaque
  subscription quota — and §5.1 exempts the judge from every budget, so there
  is no cap of any kind on judge calls.
- **Fix / Question:** Put to the user: *"Judge calibration needs a labelled
  set. Options: (a) hand-label the judge's verdicts on a fixed 30–40-case
  sample of the **dev** split run once (costs ~30–40 extra encounters against
  the free-tier daily cap); (b) hand-label synthetic (final answer, ground
  truth) pairs with no encounters, which validates the judge cheaply but not on
  real outputs; (c) defer judge calibration until evaluation scales beyond 3
  cases. Which, and what agreement level counts as 'validated'?"* Separately,
  replace "judge cost" in R5 with "judge token counts and call count", and add
  a per-run judge **call** cap.

## 22. Phase 2's interactive mode depends on an orchestrator node that does not exist until Phase 3

- **Severity:** major
- **Section:** §7 Phase 2 vs Phase 3, §5 item 9 (Q-31)
- **Problem:** Q-31 and §5 item 9 place `interrupt()` "at the orchestrator
  node", resumed with `Command(resume=...)`, backed by `MemorySaver`. Phase 2
  delivers "Patient node, gatekeeper node, interactive doctor mode", acceptance
  "interactive mode drives a real case". Phase 3 delivers the `single_doctor`
  graph — which is where the orchestrator, `route_action`, `should_continue`
  and `EncounterState` first exist. Phase 2 therefore either has to build most
  of Phase 3's graph (violating brief §0.2's phase gating and Phase 3's own
  acceptance criteria), or build a REPL that calls the patient and gatekeeper
  nodes directly — which is Q-31's explicitly **rejected** option (b).
- **Fix / Question:** Put to the user: *"Should Phase 2's interactive mode (a)
  pull the minimal `single_doctor` skeleton — `EncounterState`, `route_action`,
  a human-driven orchestrator with `interrupt()` — forward into Phase 2, with
  Phase 3 then adding only the LLM orchestrator/hypothesis nodes; or (b) ship a
  non-graph REPL in Phase 2 and move `interrupt()` to Phase 3? Q-31 chose the
  graph-based mechanism, so (a) seems implied, but it changes Phase 2's scope."*

## 23. Using `:free` OpenRouter endpoints contradicts the privacy rationale that Q-32 used to reject LangSmith

- **Severity:** minor
- **Section:** §4, §9 R3 (D-020, Q-32)
- **Problem:** Q-32 rejected LangSmith with the rationale "nothing leaves the
  machine", and §6.4 records "No LangSmith (Q-32)". But D-020 routes every
  agent role through `:free` OpenRouter endpoints, which on OpenRouter
  generally require the account-level prompt-logging/training setting to be
  enabled. If so, every prompt and completion in the project is already being
  shared with third parties — a strictly larger exposure than the one Q-32
  declined. This does not violate brief §2 (the data is public MIT-licensed
  benchmark text, not real patient data), but the plan states a privacy
  property it does not have.
- **Fix / Question:** Put to the user: *"Free OpenRouter endpoints typically
  require enabling prompt logging/training on the account. Is that enabled, and
  is it acceptable? If so, §6.4 should say 'no LangSmith; note that free
  OpenRouter endpoints may log prompts' rather than implying nothing leaves the
  machine."*

## 24. Assorted internal contradictions and unhandled edges

- **Severity:** minor
- **Section:** §3 matrix / §3.1, §2.2, §5.1, §7, Q-34
- **Problem:** Individually small, collectively the kind of thing that costs an
  afternoon each:
  1. **§3 matrix contradicts §3.1 item 1.** The matrix shows
     `match_diagnosis (judge)` with `—` under `Management_and_Follow_Up`, while
     §3.1 item 1 says that field "appear[s] on **no view except `JudgeView`**".
     Decide whether `JudgeView` carries it (it is on exactly one case, 133) and
     make the two agree.
  2. **`orch -->|invalid x3| fin` vs Q-15.** Q-15 says "2 repair retries" (=3
     attempts). The label "invalid x3" reads as three retries. Say "3 attempts
     (initial + 2 repairs)".
  3. **`RoutingError` is an unhandled fifth exit.** §2.2 has `route_action`
     raise on an unhandled return value — correct — but neither §5's stop
     conditions nor §6.3's outcome columns cover a raised exception. State
     whether the eval runner catches per-case exceptions, records a
     `crash`/`error` outcome, writes partial results, and continues to the next
     case, or aborts the run.
  4. **Q-34's cache is unusable in practice.** The cache is "hard-disabled by a
     code-level assertion for any run that writes a report", and §6.2's 3-case
     runs write a report. So every development iteration on the only cases that
     exist burns free-tier requests against the 1000/day cap (#13). Worth
     noting explicitly, and worth allowing a `--no-report --cache` iteration
     mode.
  5. **Subgraph `input_schema`/`output_schema` keys are not listed.** §2.3 says
     the panel declares them "so only named keys cross the boundary", but never
     names the keys. Given #3's `proposed_action`/`action` problem, this is the
     place the bug would bite. List them.
  6. **Concurrency 4 + a synchronous judge.** §5.1 has 4 async workers; D-021's
     `anthropic` SDK call in the judge graph is synchronous unless the async
     client is used. State which, or the judge serialises the whole runner.

---

## Summary of what I could not check

- Whether `nvidia/nemotron-3-super-120b-a12b:free` actually supports strict
  structured outputs and tool calling (D-020 asserts it; §4 says "every
  model-facing schema below depends on" it). This is the single largest
  unverified dependency in the plan and every structured schema in §4 fails
  without it. Brief §4 requires verifying support "before relying on it"; the
  plan should state that a Phase 1 live spike confirms it and what the fallback
  is if it does not hold.
- Whether OpenRouter's inline `usage: {include: true}` populates `cost` for
  `:free` models (Q-07/Q-03 both depend on it; for free models `cost` may be 0
  or absent, which would leave `api_cost_usd` and the §6.4 cost reporting
  meaningless rather than merely zero).

---

# Resolutions (Step 5)

Appended 2026-09-15 by the implementing agent. Every finding gets
`accepted` / `rejected` / `needs user decision`. Items marked **needs user
decision** are NOT resolved here — they are put to the user in
`docs/DECISIONS_OPEN_2.md` and will be recorded in `docs/DECISIONS.md`.

> **Note on tense.** An `accepted` entry below *specifies* the change; it has
> **not yet been applied to `docs/PLAN.md`**. Several accepted fixes (#1, #3,
> #10, #14) depend on how Q2-01 and Q2-02 are answered, so all 24 resolutions
> are applied in a single pass once the round-2 answers land — then a second
> fresh-context review runs per brief §1 Step 5. The only files already changed
> are `docs/PHASE_0_NOTES.md` and `docs/DECISIONS.md` (D-015, D-018), which
> carried verified factual errors and were corrected immediately.

**Independent verification.** Before responding I re-derived the reviewer's
checkable claims against `dataset/agentclinic_medqa_extended.jsonl`. **All of
them hold**, including one the reviewer could only state as a probability:

| Claim | Reviewer | Verified |
|---|---|---|
| `dx_in_results`, values only, case-insensitive | 27 | 27 |
| `dx_in_results`, keys + values | 28 | 28 |
| `dx_in_results`, keys + values, abbreviation stripped | 29 | 29 |
| Diagnosis present in `Physical_Examination_Findings` | 0 | **0** |
| Duplicate diagnoses, raw / case-insensitive | 31 over 63 / 35 over 72 | 31/63, 35/72 |
| `Test_Results` with any top-level string value | 16 | **16** (14 mixed, 2 all-string) |
| `Test_Results` containing a list | "4 list values at depth 2" | 2 cases (153, 185) |
| Dev split contains no empty-`Test_Results` case | "≈43% chance" | **Certain** — 0 under seed 20260915 |

---

## 1. `challenger_final` reopen loop — **accepted**
Real unbounded loop; the plan contradicted itself. Changes to `PLAN.md`:
`reopen_count: int` added to `EncounterState`; `challenger_final` routes
unconditionally to `finalize` when `reopen_count >= 1`; `should_continue` now
also guards the `challenger_final → PANEL` edge; §2.1 now names `hypothesis` as
the sole writer that increments `turn`.

## 2. §1.1's structural claim is false — **accepted**
The reviewer is right and this was the plan's headline guarantee. `encounter_log`
does carry the diagnosis for the flagged cases, so "no field capable of holding
ground truth" was wrong as written. §1.1 and §3.1 item 3 are rewritten to the
precise, provable property: *no `EncounterState` key is ever written from
`Correct_Diagnosis` or `Management_and_Follow_Up`, because no doctor-side view
exposes them.* Enforced by (a) a view-attribute test and (b) a key→source
allow-list test that fails when a new state key is added.

## 3. Challenger reopen authority / cost-steward veto — **needs user decision** (authority) + **accepted** (defects)
The authority question goes to the user (`DECISIONS_OPEN_2.md` Q2-01). The
schema and routing defects are accepted regardless: `CostStewardOpinion` gains
`replacement_action: Action | None`; `proposed_action`/`action` is collapsed to a
single `action` key written once by the orchestrator and amended in place by
`cost_steward`, removing the undefined no-veto path entirely.

## 4. Test-selection sub-role merged silently — **needs user decision**
Correct: no decision authorises the merge, and §1.2's description of
`single_doctor` as "no sub-roles" contradicts its own diagram. → `Q2-02`.

## 5. Rule-based red-flag detection undefined — **needs user decision**
Correct on all three counts, and the leak risk in interpretation A is real. → `Q2-03`.

## 6. Abstention scoring undecided — **needs user decision**
Correct. Q-15 and §6.3 define the columns but never the scoring rule. → `Q2-04`.

## 7. 3-case subset unsatisfiable — **accepted** (verified as fact) + **needs user decision**
Verified: under seed 20260915 the grouped dev split contains **zero** of the four
empty-`Test_Results` cases, so D-022's constraint is not merely at risk, it is
unsatisfiable. The deterministic selection rule is accepted and now specified in
§6.2. The remedy is the user's call → `Q2-05`.

## 8. `dx_in_results` rule unspecified and counts wrong — **accepted**
Verified. `PLAN.md` §3.2 now specifies the rule as a testable function
(normalise case and whitespace, strip a trailing parenthetical, match over
flattened **keys and values**), the count becomes a **computed value, never a
literal**, and the false claim that the string appears in
`Physical_Examination_Findings` is removed — it appears in **zero** cases.
`docs/PHASE_0_NOTES.md` and D-015 are corrected. The suggested secondary
`dx_tokens_in_results` flag is also accepted as a reported metric.

## 9. Leakage-test scoping unstated — **accepted**
§3.2 and Phase 1 acceptance now state the three tests explicitly: string scan
over `not dx_in_results` asserting absence; a positive test asserting the
*expected* presence for flagged cases so a change to the flag breaks loudly; and
the case-independent view/state allow-list test from #2.

## 10. Panel / `single_doctor` structural asymmetry — **accepted**
A genuine confound in the project's headline comparison. Forced stops now route
through a final `hypothesis` update and `challenger_final` (reopen disabled on
that path), making Q-16's "before every finalize" true on every path and giving
both graphs the same last-result handling.

## 11. `Objective_for_Doctor` homeless; schemas undefined — **accepted**
`objective: str` added to `EncounterState`, explicitly exempt from the §5 item 7
truncation rule. `EncounterSummary`, `Event`, `RedFlag` and `StopReason` are now
defined in §4, and §4 names the producer of every `EncounterSummary` field —
`findings` and `tests_ordered` are derived from `encounter_log` by the
`hypothesis` node, which previously had no producer.

## 12. Spend cap inert, ambiguous, cannot stop gracefully — **accepted**
§5.1 now states the cap applies to `spend_usd` only, with `test_cost_usd` a
simulated metric never compared against it. The client raises a typed
`BudgetExceeded`, caught by the orchestrator, which sets
`stop_reason="spend_cap"` and routes to `finalize` with no further LLM call. The
"is the cap inert under free models" point is folded into #13's request cap.

## 13. No daily-cap guard — **accepted**
A persistent UTC-dated request counter is checked alongside the token bucket,
with a pre-flight estimate refusing runs projected to exceed the remaining daily
allowance. 429s are classified minute-cap vs daily-cap from the response, and a
daily-cap breach aborts the run rather than degrading case by case. `n_scored`,
`n_error`, `n_abstained` and the explicit denominator are added to every
accuracy figure in `report.md`.

## 14. Reducer makes `red_flag_turn` unrecoverable — **accepted**
`red_flag_turn` is derived from the append-only `encounter_log`, not from
`red_flags`. §2.1 now names the single writer of every last-write-wins key.

## 15. `search_literature` routable before the evidence agent exists — **accepted**
§4 now states the `Action` enum is built per-run from the enabled action set;
the enabled set is recorded in run metadata and `report.md`; runs with different
action spaces are never compared. Brief §8's 2×2 configuration matrix is added
to §6.

## 16. `top_k` in pure Python disagrees with the judge — **accepted**
The judge now returns `entry_matches: list[bool]` for the differential in the
same call, and top-k is computed from that, so `top_1` and `judge_correct` are
consistent by construction.

## 17. `JudgeVerdict.correct` redundant — **accepted**
`correct` is removed. `judge_correct = match_type in {"exact","synonym"}` and
`lenient_correct = match_type != "wrong"` are derived in code, per Q-22.

## 18. Gatekeeper match granularity undefined — **accepted**
§4 now specifies: match at top level, return the matched key with its complete
sub-tree (this *is* "ordering a test panel", and §3.1 item 6's wording is
corrected so it no longer reads as forbidding that); a request naming a leaf
matches its parent. Verified the type problem is worse than D-018 recorded —
**16** cases have string values at top level, not 2, and 2 cases (153, 185)
contain lists — so the loader and gatekeeper must handle `str | dict | list` at
every level. D-018 is corrected.

## 19. "Stratified" means the opposite of what is intended — **accepted**
The most dangerous finding of the batch, because `splits.json` is committed once
and never regenerated. §6.1 now says **grouped**, specifies grouping on
`" ".join(dx.strip().lower().split())` (verified necessary: 35 groups over 72
cases case-insensitively vs 31 over 63 raw, so 5 diagnoses differing only in
capitalisation would otherwise span the splits), writes out the exact algorithm,
and adds a test that regenerating from the spec reproduces the committed file
byte-for-byte.

## 20. Q-05 pinned-provider order never chosen — **needs user decision**
Correct: `allow_fallbacks: false` with no `order` is not a specification. → `Q2-06`.

## 21. Judge calibration set undefined — **needs user decision**
Correct, and the n=3 consequence is sharp. The secondary point is **accepted**:
R5's "judge cost tracked separately" becomes "judge token and call counts", since
subscription auth exposes no per-call price, and a per-run judge **call** cap is
added. → `Q2-07`.

## 22. Phase 2 interactive mode depends on Phase 3 — **needs user decision**
Correct phase-gating conflict. → `Q2-08`.

## 23. `:free` endpoints contradict Q-32's privacy rationale — **needs user decision**
The factual premise (that free endpoints may require prompt logging) could not be
confirmed from OpenRouter's rate-limit documentation, so the question put to the
user includes verifying it. → `Q2-09`.

## 24. Assorted contradictions — **accepted** (all six)
1. `JudgeView` carries `Management_and_Follow_Up`; the §3 matrix cell is
   corrected to ✅ so it agrees with §3.1 item 1.
2. Edge label becomes "3 attempts (initial + 2 repairs)" per Q-15.
3. §5 gains an explicit rule: the eval runner catches per-case exceptions,
   records outcome `crash`, writes partial results, and continues.
4. A `--no-report --cache` iteration mode is added so development does not burn
   free-tier requests.
5. The panel subgraph's `input_schema` / `output_schema` keys are now listed.
6. The judge uses `AsyncAnthropic` so it does not serialise the runner.

---

## Response to "Summary of what I could not check"

Both items **accepted** as plan additions.

1. **Structured-output support for `nvidia/nemotron-3-super-120b-a12b:free`.**
   Partially verified from OpenRouter's live `/api/v1/models`: the model
   advertises `structured_outputs`, `response_format` and `tools` in
   `supported_parameters`. Advertised support is not the same as working
   support, and brief §4 requires verifying before relying on it, so Phase 1
   now carries an explicit live spike as an acceptance criterion, with the
   fallback path (drop to `response_format` JSON mode, then to prompt-and-parse
   with the Q-15 repair loop) written into §4.
2. **Whether `usage.cost` populates for `:free` models.** Cannot be checked —
   no `OPENROUTER_API_KEY` is set. The same Phase 1 spike now covers it, and §6.4
   states that if cost is absent or zero for free models, `api_cost_usd` is
   reported as `0.0 (free tier)` rather than silently implying a measured value.

## Status

7 blockers: 3 accepted outright (#1, #2, #7-partial), 4 require user decisions
(#3, #4, #5, #6) plus #7's remedy. 14 majors: 11 accepted, 3 require user
decisions (#20, #21, #22). 3 minors: 2 accepted, 1 requires a user decision (#23).

**Blockers remain open until the user answers `docs/DECISIONS_OPEN_2.md`.**
Per brief §1 Step 5, a second review round with a fresh subagent runs once the
plan is updated.
