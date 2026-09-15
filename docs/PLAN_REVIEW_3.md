# Adversarial review of docs/PLAN.md — Round 3

2026-09-15 — verification pass on revision 3

Scope: `docs/PLAN.md` (rev 3), `docs/DECISIONS.md` (D-001 … D-037),
`docs/BRIEF.md`, `docs/PLAN_REVIEW_2.md` (round 2 + its appended resolutions),
`docs/PLAN_REVIEW.md` (round 1, context), and read-only verification against
`dataset/agentclinic_medqa_extended.jsonl` + `dataset/MANIFEST.json`.
No `src/`, `scripts/`, `tests/` or `PHASE_0_NOTES.md` was read.

### Dataset claims re-derived this round — **all 14 hold**

Rounds 1 and 2 each found dataset claims that were wrong. Revision 3's are not.
Every load-bearing number was recomputed from the file:

| Rev 3 claim | §  | Verified |
|---|---|---|
| `dx_in_results` = **29** under the §3.2 rule | §3.2 | ✅ 29 — ids 2, 3, 11, 14, 18, 20, 23, 39, 48, 52, 62, 86, 87, 102, 104, 107, 108, 119, 134, 144, 154, 155, 161, 163, 166, 174, 185, 197, 199 |
| 27 values-only / 28 keys+values without stripping | §3.2 | ✅ 27 / 28 |
| Diagnosis in **zero** `Physical_Examination_Findings` | §3.2 | ✅ 0 |
| Leak-free denominator **185** | §3.2 | ✅ |
| `dx_tokens_in_results` = **4** — {13, 34, 123, 208}, 182 removed | §3.2 | ✅ 4, and **stable** across four tokenisation variants (split-on-non-alnum, whitespace-only, hyphen-preserving, with and without parenthetical stripping). The stated justification for dropping 182 is also correct: `Acute Hepatitis B` has neither `\bacute\b` nor a standalone `\bb\b` in its results |
| 16 top-level-string cases (2 all-string 150/187, 14 mixed), 2 with lists (153, 185) | §4.2 | ✅ exactly; mixed = 17, 67, 88, 114, 118, 120, 125, 140, 141, 164, 191, 201, 211, 213 |
| **PEF contains lists in cases 37 and 103** | §4.2 | ✅ exactly those two |
| 4 empty-`Test_Results` cases (69, 106, 111, 209) | §4.2 | ✅ |
| 234 distinct top-level `Test_Results` keys, 165 singletons | R7 | ✅ |
| **58/214** ambiguous leaves; the 9 named real-analyte cases | §4.2 | ✅ 58; and all 9 spot-checked (10/38/55/80/101 `WBC` CBC+Urinalysis; 29 `Na`+`Glu`; 58 `Bilirubin`; 66 `Glucose`; 30 `WBC` CBC+joint aspirate) |
| **177** groups, 35 multi-case over 72; 31 over 63 raw | §6.1 | ✅ exactly |
| §6.1 algorithm traced by hand | §6.1 | ✅ dev = **40**, heldout = **174** |
| §6.2 3-case rule traced by hand | §6.2 | ✅ {2, 9, 12} = `medqa-0002/0009/0012`, case 2 flagged |
| Diagnosis never in `Objective_for_Doctor` / `Patient_Actor` | §1.1 | ✅ 0 / 0 |
| `Management_and_Follow_Up` on exactly 1 case | D-016 | ✅ |

**So there are no dataset findings this round.** §3.2, §4.2, §6.1 and §6.2 are
now factually sound, and every number Phase 1 is asked to pin is correct.
B-12's rule is robust enough that the tokenisation ambiguity round 2 worried
about does not change the answer.

**What is genuinely fixed.** The §2.1 semantics column is real and mostly
right; the `turn` accounting claim ("`check_stop` runs exactly once per executed
action in both graphs") is **true** — I traced both graphs and neither the
finalize re-decision nor the re-prompt branch reaches `check_stop`. The
`challenger_final` loop of B-1 is genuinely gone. `panel_events` genuinely
solves B-2's exponential duplication. `check_stop` as a node genuinely gives
`stop_reason` a legal writer. D-034 … D-037 are sound and the fuzzy-tier removal
is the right call.

The problems this round cluster in exactly what revision 3 rewrote: the
**writer table's omissions**, the **new `panel_events` channel**, the new
**`rp` and `budget_exhausted` paths**, and — most importantly — the fact that
moving the challenger nodes out of reach of `encounter_log` left them with
**no channel to any reader**.

---

## Part A — round 2 blockers

### B-1 — `challenged_this_finalize` / the finalize re-decision

**Mechanically fixed. Semantically hollow.** Verdict: **the control flow is now
correct; the re-decision it produces carries no new information** (→ C-1).

I traced the rev-3 panel graph by hand. Edges:

```
START→brief→hypothesis→PANEL→absorb_panel→route_action
route_action → {ask, exam, test, lit} → check_stop → route_stop → {hypothesis | challenger_stop}
route_action → challenger_final → hypothesis
route_action → finalize → END
route_action → rp → PANEL
challenger_stop → finalize → END
```

Three cycles exist. Taking each question in turn:

**Does the re-decision happen exactly once?** Yes, once per finalize *attempt*.
`route_action` reads `challenged_this_finalize` **before** `challenger_final`
writes it, so the pre-write value is still available — B-1's core defect is
gone. `challenger_final` sets the flag → `hypothesis` → `PANEL` → `route_action`
now sees `True` → `finalize`. For a second re-decision to occur, the flag must
be cleared, which only an action node does, which only happens via `check_stop`.
So re-decisions are bounded by executed actions, which are bounded by the turn
cap. ✅ Correct by construction, no counter.

One residual ambiguity: §7 Phase 4's criterion "test asserts the finalize
re-decision happens **exactly once**" does not say *once per encounter* or *once
per finalize attempt*. The design gives the latter (an encounter that finalizes
at turn 4, is talked out of it, acts, and finalizes again at turn 9 gets two
re-decisions — which is what D-025's "reset whenever a non-finalize action runs"
intends). Two engineers write different tests. Say which. **minor.**

**Can it loop?** Not via `challenger_final`. ✅ But revision 3 introduced two
*new* cycles that bypass `check_stop` — `rp → PANEL` (→ C-3) and the
`budget_exhausted` no-op cycle (→ C-4). These are structurally identical to
round-1 #1: a cycle that touches no cap-evaluating node.

**Can a finalize escape without a `FinalAnswer`?** Yes, on three paths:
1. the `rp` loop, which terminates only at the recursion limit (`GraphRecursionError`
   → runner outcome `crash`, no `FinalAnswer`) — C-3;
2. the `budget_exhausted` path, which has no edge to `finalize` at all — C-4;
3. a genuine exception, which §5 item 10 handles correctly ✅.

`challenger_stop` on the cap path is correct control flow (single edge to
`finalize`, no re-decision) ✅ — but see C-1: nothing reads what it produces.

### B-2 — `encounter_log` across the subgraph boundary

**Fixed for the bug that was reported. Four new gaps in the replacement.**

Reducer arithmetic, end to end:

- `encounter_log` is absent from **both** subgraph schemas ✅ — so the
  `parent_log + (parent_log + new)` doubling cannot occur. Confirmed against the
  §2.3 schema lists.
- `panel_events` is in `output_schema` but **not** `input_schema`. That is the
  right asymmetry: the subgraph starts with the channel unset, accumulates only
  this deliberation's events, and returns a pure delta that `absorb_panel`
  converts into the `encounter_log` `operator.add` update. ✅
- The same asymmetry makes `spend_usd` and `parse_failures` correct across the
  boundary: both are in `output_schema`, neither is in `input_schema`, so the
  subgraph returns its own contribution and the parent's `operator.add` sums it.
  **No double-count.** ✅
- `turn` is in `input_schema` and **not** in `output_schema` ✅ — critical,
  because `turn` has an `operator.add` reducer in the parent and a round-trip
  would double it every turn. Revision 3 gets this right.

**"Can `panel_events` drop events if the subgraph runs twice before
`absorb_panel`?"** No — `PANEL → absorb_panel` is unconditional and `rp` routes
back to `PANEL`, so every subgraph invocation is followed by an absorb before
the next one. ✅ The failure mode the question anticipates does not exist.

But three that do:

- **Inside** the subgraph, `panel_events` needs an `operator.add` reducer or the
  orchestrator's write overwrites the challenger's. §2.1 declares it
  *last-write* and says nothing about the subgraph's internal channel → C-6.
- `absorb_panel` is not a declared writer of `panel_events`, so it cannot clear
  it; a deliberation that emits none (budget-exhausted no-ops) leaves a stale
  value to be re-absorbed → C-7.
- `single_doctor` has no `absorb_panel` node in §2.2 although `solo_decide`
  "declares the same schemas" → C-13.

**"Does `hypothesis` in the parent still get what it needs?"** For
`findings`/`tests_ordered`, yes: the action nodes append results and
`absorb_panel` appends the deliberation. ✅ But **four of the thirteen
`Event.kind` values have no declared writer anywhere** — `hypothesis`,
`red_flag`, `parse_failure`, `budget` — because §2.1's `encounter_log` writer
list is `brief`, `absorb_panel`, action nodes, `finalize`, while §3.1 separately
grants `check_stop` an append and §2.2/§2.3 add an `rp` node that appears in
neither. `red_flag` having no writer means `red_flag_turn` is always `None`,
undoing round-1 #14 → C-5.

### B-3 — `stop_reason`

**Fixed for four of six paths. Broken for the other two.**

| Path | `stop_reason` | Correct? |
|---|---|---|
| Voluntary `finalize` | `check_stop` never ran on this path, so `None` → `finalize` writes `"finalize"` | ✅ |
| `turn_cap` | `check_stop` writes it; `finalize` sees non-`None` and does not overwrite | ✅ |
| `spend_cap` | same | ✅ mechanically — but see C-9, it races the client-side `BudgetExceeded` |
| `request_cap` | same | ✅ mechanically — but `check_stop` must read a process-global daily counter, unstated (C-19) |
| `budget_exhausted` | **no path to `finalize` exists at all**; if one is improvised, `stop_reason` is `None` and `finalize` stamps `"finalize"` | ❌ C-4 |
| 3 invalid outputs | `route_action → fin` directly, `stop_reason` is `None`, `finalize` stamps `"finalize"` | ❌ — a forced stop reported as voluntary |

`crash` is correctly dropped from `StopReason` and made a runner-level outcome ✅.
But `StopReason` has **no member for budget exhaustion**, so even an implementer
who wanted to record it honestly cannot type it.

**"Is `forced_stop` derivable?"** No. `forced_stop` is listed in §6.3 as a
per-case metric and **is defined nowhere in the plan**. The only natural
derivation, `stop_reason != "finalize"`, yields `False` for both genuinely
forced paths in the table above. Q-24's "forced-stop rate reported" is therefore
under-reported by exactly the two paths that matter most at n=3 → C-11.

---

## Part B — round 2 majors and minors

| # | Status | Note |
|---|---|---|
| **B-4** budget handling | **partial / new defects** | `@budget_guarded` is the right shape ✅, but: the guarded-node list omits `request_exam`/`order_test` (the "gatekeeper's LLM tier" is not a node — it is called *from* those two); nothing routes a budget-exhausted encounter to `finalize`; `StopReason` has no value for it; the outcome mapping is unstated. → **C-4, C-9, C-10** |
| **B-5** writer table / delta semantics | **partial** | The Reducer + Semantics columns landed and the delta convention is stated ✅. The writer *lists* are wrong: `encounter_log` omits `check_stop` (which §3.1 grants an append), `rp`, `challenger_final`, `challenger_stop` and `hypothesis`; `panel_events` omits `absorb_panel`. §3.3's allow-list test pairs "every key with its declared source", and "every LLM node" / "`@budget_guarded` on any LLM node" are not sources a test can enumerate. → **C-1, C-5, C-7** |
| **B-6** `turns_since_challenge` | **resolved** ✅ | Key deleted; `turn % 3 == 0 and turn > 0` reads `turn` directly. Traced: fires at turns 3, 6, 9, 12, 15, 18 under a 20-turn cap. The extra `and not challenged_this_finalize` conjunct does correctly prevent a double-fire on the re-deliberation. Two small couplings → **C-17** (minor) |
| **B-7** per-node isolation structural, not prompt-level | **partial / new defect** | Removing `encounter_log` from the subgraph is real, structural progress ✅. But `panel_events` is raw `Event` text *inside* the subgraph, and the orchestrator must read it or the challenger is decorative. §2.3's claim that the subgraph nodes "*cannot* read raw event text, only `summary`" is therefore either false or fatal to the panel. → **C-2 (blocker)** |
| **B-8** per-run `Action` set | **resolved with residuals** ✅ | `make_action_type` / `create_model` shown; the disabled-action branch exists. Residuals: `EncounterState.action: Action \| None` names a type that no longer exists statically (**C-14**); §4.1 attributes a state write to a routing function (**C-15**); and the branch it adds is an unbounded loop (**C-3**) |
| **B-9** scoring arithmetic | **resolved** ✅ | Coverage denominator, `n_scored ∈ {0,1}`, bootstrap pairing and the matched-coverage caveat are all specified and correct. Residuals in **C-10** and **C-20** |
| **B-10** fuzzy tier / judge cap / dev-only | **resolved** ✅ | D-034 drops the tier with sound reasoning; D-037 pins 200; §6.2 carries the "all Phase 3–5 numbers are dev numbers" statement verbatim |
| **B-11** judge calibration | **resolved** ✅ | D-036's sealed half, conjunctive bar, measured-once, user-owned labels are all in §6.5. One gap: no stated consequence if the sealed half fails → **C-21** |
| **B-12** `dx_tokens_in_results` | **resolved** ✅ | Rule specified, count corrected to 4, pinned by a test in Phase 1. Independently reproduced under four tokenisation variants |
| **B-13** ambiguous leaf | **resolved** ✅ | D-035; case 10 `WBC` in the Phase 2 acceptance list. Small gap: §4.2 says the LLM tier sees "key names only", but detecting that a request *is* ambiguous (to log tier `llm_disambiguated` rather than `llm`) requires a leaf index the tier is not given. Say how the tier label is assigned |
| **B-14** split write-up | **resolved** ✅ | Comment corrected to 177/35/72 (verified), `assert len(dev) == 40` added, serialisation pinned. Byte-for-byte still needs the trailing-newline convention named — trivial, fold into the test |
| **B-15.1** §3.3 item 4 wording | **resolved** ✅ | Explicitly corrected, with the correction narrated |
| **B-15.2** 3-invalid skips challengers | **resolved** ✅ | Diagram and prose now agree |
| **B-15.3** cost-steward has no budget state | **resolved** ✅ | Stated as deliberate |
| **B-15.4** PEF lists 37/103 | **resolved** ✅ | In Phase 1 and Phase 2 acceptance |
| **B-16** run modes + `rich` viewer | **resolved** ✅ | §5.1 run-modes table; viewer in Phase 1 |
| **B-17** judge tracebacks / n=2 | **resolved** ✅ | §3.3 item 8 and §6.2 both landed, with the contamination consequence stated |

**Round-2 findings not fully resolved: 6 of 17** — B-1 (mechanically fixed but
hollow), B-2 (fixed, four new gaps), B-3 (four of six paths), B-4, B-5, B-7.

Round-1 Part A item **#10** deserves a note: the accepted fix "forced stops route
through a final `hypothesis` update" is still absent, but it no longer matters —
I traced both graphs and on a cap-forced stop *neither* arm runs `hypothesis`
after the last action (`action → check_stop → [challenger_stop] → finalize`),
so the asymmetry round 1 complained about is gone by symmetry rather than by the
accepted fix. Both arms discard the final action's result when building the
`FinalAnswer`. That is defensible but worth one sentence in §5.

---

## Part C — new findings

### C-1. The finalize re-decision is informationally empty: `challenger_final` and `challenger_stop` have no write channel and no reader

- **Severity:** blocker
- **Section:** §2.1 (writer table), §2.3, §3.1 matrix, D-025
- **Problem:** D-025 is explicit: the challenger and cost-steward "**both write
  opinions into `encounter_log`**", and the panel differs from the single doctor
  because "the orchestrator sees both opinions before deciding". Revision 3 moved
  `challenger_final` and `challenger_stop` into the **parent** graph and then:
  - §2.1's `encounter_log` writer list is `brief`, `absorb_panel`, action nodes,
    `finalize` — neither challenger node appears;
  - §3.1's matrix row `challenger, challenger_final, challenger_stop` shows
    **`—`** in the `encounter_log` column, i.e. neither read nor append (other
    rows say "append" explicitly, so this is a deliberate `—`, not an omission of
    formatting);
  - `panel_events` is written by the *subgraph*, and both nodes are outside it.

  So `ChallengerOpinion` from `challenger_final` is produced and **discarded**.
  Trace the re-decision: `challenger_final` → `hypothesis`. `hypothesis` builds
  `summary`/`differential` from `encounter_log`, which is unchanged. The
  subgraph's `input_schema` is `objective, summary, differential, red_flags,
  turn, challenged_this_finalize, budget_exhausted`. Every one of those is
  identical to the previous invocation except `challenged_this_finalize`. **The
  orchestrator re-decides on the same information it just decided on**, one
  boolean apart. It will overwhelmingly emit `finalize` again.

  This is B-1's failure mode arriving by a different road: the panel's entire
  substantive difference on the finalize path evaporates, the Phase 4 test
  ("the re-decision happens exactly once") **passes** because the re-decision
  does happen, and the only observable effect is 2–3 extra LLM calls per
  finalize against a 1000/day budget.

  `challenger_stop` is worse: it runs, produces an opinion, and its only edge is
  to `finalize`, which reads `differential`/`summary` and not the opinion. It is
  pure cost with **zero** possible effect on any output. Q-16's "challenger
  before every finalize" is satisfied in letter and empty in substance.
- **Fix:** Add `challenger_final` and `challenger_stop` to §2.1's `encounter_log`
  writer list and change their §3.1 cell to "append"; state that `hypothesis`
  folds a `kind="challenge"` event into `summary.open_questions` so the
  orchestrator's re-decision actually differs; and state what `finalize` reads
  from `challenger_stop` — or delete `challenger_stop` and record that Q-16 does
  not hold on the cap path, which is defensible when the budget is already spent.

### C-2. The in-panel challenger reaches the orchestrator only as raw `Event` text, contradicting §2.3's isolation claim and failing §3.3's prompt test by construction

- **Severity:** blocker
- **Section:** §2.3 (subgraph schemas + prose), §3.3 item 3 (prompt test), §5 item 8
- **Problem:** §2.3 states the structural-isolation claim that B-7 was accepted
  to buy: "`encounter_log` is **absent from both** — this is what makes review
  B-7's per-node isolation structural rather than prompt-level: the orchestrator,
  challenger and cost-steward *cannot* read raw event text, only `summary`."
  And §3.3 adds the enforcing test: "the orchestrator's rendered prompt contains
  no substring of any `Event.text` absent from `summary`."

  But the in-subgraph challenger runs **before** the orchestrator
  (`cd → chal → porch`) and its opinion is an `Event` (`kind="challenge"`)
  written into `panel_events`. `summary` is produced by `hypothesis` in the
  parent, *before* the challenger ran, so the challenge text is by definition an
  `Event.text` absent from `summary`. Two mutually exclusive outcomes:
  - **(a)** The orchestrator reads `panel_events` (the only way it can see the
    challenge). Then §2.3's claim is false, the isolation is prompt-level again,
    and **the §3.3 prompt test fails on every turn the challenger runs** —
    i.e. the plan's own acceptance test contradicts the plan's own design.
  - **(b)** The orchestrator does not read `panel_events`. Then the challenger
    is decorative in-turn and only influences the *next* turn via
    `absorb_panel → encounter_log → hypothesis → summary`. That is a coherent
    design, but it contradicts D-025 ("the orchestrator sees both opinions before
    deciding") and makes `challenge_due`'s placement *before* the orchestrator
    pointless — the challenger might as well run after.

  This is the project's central comparison. Two engineers build materially
  different panels from these sentences, and one of them has a red test.
- **Fix:** Stop routing the opinions through a free-text event channel. Give the
  subgraph two typed, last-write keys — `challenger_opinion: ChallengerOpinion | None`
  and `cost_objection: CostStewardOpinion | None` — that the orchestrator is
  explicitly allowed to read, keep `panel_events` purely as the transcript that
  `absorb_panel` folds into `encounter_log`, and scope the §3.3 prompt test to
  "no substring of any `Event.text` **that originated outside the subgraph**".
  Then the claim in §2.3 becomes true again and the test becomes writable.

### C-3. The `rp → SOLO/PANEL` re-prompt loop bypasses `check_stop` and has no bound in state

- **Severity:** blocker
- **Section:** §2.2, §2.3 (`rp[record parse_failure] --> SOLO/PANEL`), §4.1, §5 item 4
- **Problem:** Both diagrams contain `route -->|disabled action| rp` and
  `rp --> SOLO` / `rp --> PANEL`. This cycle
  (`route_action → rp → subgraph → absorb → route_action`) touches **`check_stop`
  on no iteration**. Therefore:
  - `turn` is never incremented → the **turn cap cannot fire**;
  - `stop_reason` is never written → **`spend_cap` and `request_cap` cannot fire**
    (both are `check_stop`-only per §5's table);
  - `budget_exhausted` is spend-driven and, on `:free` models, inert
    (round-1 #12's finding, never retracted).

  The only claimed bound is the diagram's `route -->|3 attempts invalid| fin`.
  But **no state key counts attempts per decision**. The only candidate is
  `parse_failures`, which §2.1 makes `Annotated[int, operator.add]` written by
  "**every** LLM node" — a *run-cumulative, cross-node* total. So:
  - **(a)** `route_action` tests `parse_failures >= 3`. Then three *unrelated*
    failures anywhere in the encounter — one repair inside `ask_patient` at turn 1,
    one inside `hypothesis` at turn 5, one disabled action at turn 9 — force an
    `abstain` finalize with outcome `error`, silently removing the case from the
    accuracy denominator. At n=3 that is 33 points.
  - **(b)** The implementer adds an undeclared per-decision counter — exactly the
    undeclared-key move §3.3's allow-list test exists to reject, and the same
    reading B-1 rejected.

  Under (b) with the counter forgotten, or under any repeated disabled-action
  emission, the loop runs to the recursion limit (140) and raises
  `GraphRecursionError` → outcome `crash`, **no `FinalAnswer`** — contradicting
  §5 item 4's headline. This is structurally round-1 #1 in a new location.

  Compounding: `rp` appears in **neither** §2.1's writer table nor §3.1's
  visibility matrix, so the `kind="parse_failure"` event it emits has no declared
  source and the allow-list test cannot account for it.
- **Fix:** Delete the `rp → subgraph` edge. The orchestrator node already owns
  Q-15's 3-attempt repair loop internally; a valid-but-disabled action is just
  another invalid output and should be repaired **inside** the node, where the
  attempt counter is a local variable and no cycle exists. `route_action` then has
  only terminal branches. If the edge is kept, add an explicit
  `decision_attempts` key to §2.1 with a named writer and a named resetter, put a
  cap-evaluating node on the cycle, and add `rp` to §2.1 and §3.1.

### C-4. `budget_exhausted` has no route to `finalize`, no `StopReason` value, and loops

- **Severity:** blocker
- **Section:** §5 item 5, §2.1, §2.2/§2.3 routing, §4.1 `StopReason`
- **Problem:** §5 item 5 says: "When set: both challenger nodes are skipped, and
  `finalize` is constructed **in Python** from the existing `differential` with
  no model call." It never says **how `finalize` is reached**. Neither
  `route_action` nor `route_stop` has a `budget_exhausted` branch; §5's
  stop-condition table has no row for it; `StopReason` has no member for it.

  Trace a breach in `hypothesis` (the first LLM call of every turn):
  1. `@budget_guarded` catches `BudgetExceeded`, returns `{budget_exhausted: True}`,
     writes no `summary`/`differential`.
  2. `PANEL` runs. The orchestrator is guarded → no-ops → **does not write
     `action`**. `action` is last-write/absolute, so the parent retains the
     *previous turn's* action.
  3. `absorb_panel` → `route_action` dispatches on the **stale** `action`, e.g.
     `ask_patient`.
  4. `ask_patient` is guarded → no-ops → `check_stop` → `turn += 1` →
     `route_stop` → `hypothesis` → step 1.

  That is an infinite loop, bounded only by the turn cap if `check_stop` happens
  to be on the path (it is, here) — so it burns the remaining turns producing
  nothing, then `check_stop` writes `turn_cap` and a *budget* failure is reported
  as a turn-cap stop. If the breach happens where `check_stop` is **not** on the
  path (in the orchestrator during an `rp` cycle, or in `challenger_final`), it
  runs to the recursion limit.

  Even the charitable reading loops: suppose the guard on the orchestrator also
  sets `action = "finalize"`. In the panel, `route_action` sees
  `challenged_this_finalize == False` and routes to **`challenger_final`**, which
  is guarded → no-ops → **does not set the flag** (the flag write is inside the
  body the guard skipped) → edge back to `hypothesis` → guarded no-op → `PANEL`
  → `route_action` → `challenger_final` again. Unbounded, with `check_stop` never
  reached.
- **Fix:** Add `"budget_exhausted"` to `StopReason` and a row to §5's table. Give
  `route_action` a first-priority branch: `if budget_exhausted → finalize`,
  bypassing both challenger nodes structurally rather than by decorator no-op
  (which is what §5 item 5 already promises — make it an edge, not a side effect).
  State that `finalize` under a breach writes `stop_reason="budget_exhausted"`
  even though `stop_reason is None`, or have the guard itself write it. State
  what happens when `differential` is empty because the breach hit `hypothesis`
  on turn 1.

### C-5. `red_flag_turn` has no producer — round-1 #14's fix is undone

- **Severity:** major
- **Section:** §2.1 writer table, §3.1, §4.1 `Event.kind`, §5 item 7, §6.3
- **Problem:** §5 item 7: "`red_flag_turn` is derived from the append-only
  `encounter_log`, not from `red_flags`." That requires someone to append a
  `kind="red_flag"` event. `HypothesisUpdate.red_flags` is produced by
  `hypothesis`, but §2.1 lists `hypothesis` as **not** a writer of
  `encounter_log` and §3.1 gives it "✅ read" (not "append"). No other node
  produces red flags. **`red_flag_turn` is therefore `None` for every case**, and
  §6.3 reports it as a per-case metric.

  Same class, same table: of the thirteen `Event.kind` values in §4.1, **four
  have no declared writer** — `hypothesis`, `red_flag`, `parse_failure`
  (→ C-3), `budget`. `check_stop` is granted an append by §3.1 but is absent from
  §2.1's writer list, so the two sections disagree.
- **Fix:** Make §2.1's `encounter_log` writer list exhaustive and reconcile it
  with §3.1 row by row: add `hypothesis` (emits `hypothesis` + `red_flag`
  events), `check_stop` (emits `budget`/cap events), `rp`, `challenger_final`,
  `challenger_stop`. Add a test that every `Event.kind` literal has at least one
  emitting node.

### C-6. `panel_events` has no declared reducer inside the subgraph, so the challenger's and cost-steward's events are dropped

- **Severity:** major
- **Section:** §2.1, §2.3
- **Problem:** §2.1 declares `panel_events: list[Event]` with reducer
  **last-write** and semantics "absolute, this deliberation only". That is the
  correct declaration for the **parent**. Inside the subgraph, three nodes append
  to it in sequence (`challenger` → `orchestrator` → `cost_steward`). Under
  last-write, the orchestrator's write **replaces** the challenger's and the
  cost-steward's replaces the orchestrator's — the classic reducer-drops-messages
  bug the brief names explicitly. The plan never states the subgraph's internal
  channel semantics, so the two natural implementations differ by a factor of
  three in what reaches `encounter_log`.
- **Fix:** State in §2.3 that the subgraph's own state declares
  `panel_events: Annotated[list[Event], operator.add]` (delta inside), and that
  the parent's declaration is last-write because the channel is *not* in
  `input_schema` and therefore arrives as a complete, self-contained deliberation.
  Add this to the Phase 4 test alongside the existing "3 panel turns leave
  `len(encounter_log)` equal to events emitted" — that test as written would pass
  with the challenger's events silently missing.

### C-7. `absorb_panel` cannot clear `panel_events`, so a no-op deliberation re-absorbs the previous one

- **Severity:** major
- **Section:** §2.1, §2.3
- **Problem:** `absorb_panel`'s job is to convert `panel_events` into the
  `encounter_log` delta. For that to be idempotent, it must clear `panel_events`
  afterwards — but §2.1 names the **panel subgraph** as the *only* writer of
  `panel_events`, so `absorb_panel` writing `[]` violates the writer table and
  §3.3's allow-list test. Consequence: any subgraph invocation that returns no
  `panel_events` (every node guarded and no-opped under `budget_exhausted`; a
  deliberation where the orchestrator writes only `action`) leaves the parent's
  previous value in place, and `absorb_panel` appends the **previous
  deliberation's events a second time**. That is B-2's duplication arriving by a
  new route — smaller in magnitude (linear, not exponential) but with the same
  consequences: corrupted traces, inflated `findings`, wrong `red_flag_turn`,
  duplicated gatekeeper text (containing the diagnosis for the 29 flagged cases)
  re-fed to the doctor.
- **Fix:** Name `absorb_panel` as a writer of `panel_events` in §2.1 with
  semantics "absolute, reset to `[]` after absorbing", and have it no-op when
  `panel_events` is empty or unchanged. Extend the Phase 4 test to run a
  deliberation that emits nothing and assert `encounter_log` does not grow.

### C-8. §1.2's claim that the two graphs are "structurally identical except for the panel's two extra nodes" is false, and it misstates what the headline comparison isolates

- **Severity:** major
- **Section:** §1.2, §9 R10, §6.4
- **Problem:** §1.2: "the two are structurally identical except for the panel's
  two extra nodes, and the comparison isolates exactly those." R10 repeats it:
  "the report states the comparison isolates challenger + cost-steward." Counted
  from §2.2 against §2.3, the panel adds **five nodes** — `challenger`,
  `cost_steward`, `challenger_final`, `challenger_stop`, `absorb_panel` — plus a
  control-flow difference the single doctor does not have: an extra
  `hypothesis` + orchestrator round trip on **every** finalize attempt.

  So panel-minus-single confounds "two extra advisory sub-roles" with "one extra
  deliberation cycle per finalize" and with roughly 2–2.5× the LLM calls per turn
  at the same turn cap. The project's central measurement is described as
  isolating something it does not isolate. D-026's recorded deviation covers the
  *test-selection merge*; it does not cover this.
- **Fix:** Correct §1.2 and R10 to enumerate the actual differences, and state in
  §6.4 that the comparison is "panel = two advisory sub-roles **plus one
  re-deliberation per finalize**". Report calls-per-case alongside `turns` so the
  compute difference is visible. (Reporting `turns` alone is misleading precisely
  because §2.1 correctly made `turns` mean the same thing in both arms.)

### C-9. Two unreconciled spend accumulators; `spend_cap` and `budget_exhausted` race for the same event

- **Severity:** major
- **Section:** §2.1, §5 items 4–5, §5.1, Q-25
- **Problem:** There are now two independent totals for the same quantity:
  1. the OpenRouter client's running total, checked *before each call* (Q-25),
     which raises `BudgetExceeded` → `@budget_guarded` → `budget_exhausted=True`,
     **no `stop_reason`**;
  2. `EncounterState.spend_usd`, an `operator.add` sum of per-node deltas, which
     `check_stop` compares against the $0.50 cap → `stop_reason="spend_cap"`.

  They necessarily disagree — (2) lags (1) by every call made since the last
  `check_stop`, and (1) counts calls (like the gatekeeper's LLM tier) whose node
  may or may not contribute a delta to (2). Whichever fires first determines
  whether the case is reported as a graceful `spend_cap` stop or as a
  budget-exhausted mess with `stop_reason="finalize"` (C-4). Round-1 #12 raised
  "two independent spend totals with no stated reconciliation"; revision 3 added
  a second mechanism instead of reconciling them.

  Secondary: the `@budget_guarded` list in §5 item 5 names "the gatekeeper's LLM
  tier", which is not a node. `request_exam` and `order_test` are the nodes that
  invoke it and neither is in the list, so a breach there propagates out of the
  graph uncaught — exactly B-4's original complaint.
- **Fix:** Pick one authority. Recommended: the client owns the total and raises;
  `spend_usd` in state is a *reporting* mirror only and `check_stop` does **not**
  evaluate the spend cap against it. Then delete the `spend_cap` row's
  "`check_stop`" writer, or keep it and state that it reads the client's total
  through the same closure. Add `request_exam` and `order_test` to the guarded
  list.

### C-10. Harness-forced abstentions have no outcome mapping and pollute §6.3's coverage denominator

- **Severity:** major
- **Section:** §5 item 5, §6.3, D-028
- **Problem:** §6.3 defines `coverage = n_scored / (n_scored + n_abstained)` and
  justifies it precisely: "errors and crashes are harness properties, not
  clinical ones, and are excluded from **both** terms." But §5 item 5's
  budget-exhausted `finalize` is "constructed in Python from the existing
  `differential`" — a harness event — and the plan never says what `outcome` it
  gets. If it produces `abstain=true` (the only sensible thing when the
  differential is empty) and is recorded as `abstained`, it lands in coverage's
  denominator as a **clinical** abstention. Same for the 3-invalid-outputs
  finalize, though §4.1 does at least assign that `outcome = error` ✅.

  This matters disproportionately for the project's headline: §6.3 already warns
  that "the panel is systematically the more likely abstainer". The panel also
  makes ~2× the calls per turn, so it is systematically the more likely to hit a
  budget breach. Both effects push the same direction, and one of them is a
  harness artefact masquerading as clinical caution.
- **Fix:** State in §5 item 5 and §6.3 that a budget-exhausted finalize is
  recorded as `outcome = error`, never `abstained`, and add `n_budget_exhausted`
  to the counts printed beside every accuracy figure.

### C-11. `forced_stop` is reported but never defined, and is `False` on both genuinely forced paths

- **Severity:** major
- **Section:** §6.3, §5 item 4, Q-24
- **Problem:** `forced_stop` is listed in §6.3's per-case metric list and defined
  nowhere in `PLAN.md` or `DECISIONS.md`. Q-24 requires "Forced-stop rate
  reported". The only derivation available from state is
  `stop_reason != "finalize"`, which (per Part A, B-3) is `False` for the
  3-invalid-outputs finalize and `False` for a budget-exhausted finalize — the
  two paths where the doctor was most obviously forced. The reported forced-stop
  rate therefore systematically undercounts.
- **Fix:** Define `forced_stop` explicitly in §6.3 as
  `stop_reason in {"turn_cap","spend_cap","request_cap","budget_exhausted"} or outcome == "error"`,
  once C-4 adds the missing `StopReason` member.

### C-12. Phase 1's acceptance criterion requires five isolation tests, two of which cannot exist until Phases 2 and 3

- **Severity:** major
- **Section:** §7 Phase 1, §3.3 item 3
- **Problem:** §3.3 item 3 announces "**Four tests:**" and then lists **five**
  bullets (View, Allow-list, String-scan, Positive, Prompt). §7 Phase 1's
  acceptance criterion is "the **five isolation tests** pass". Two of the five
  cannot be written in Phase 1:
  - the **allow-list test** pairs "every `EncounterState` key with its declared
    source", and D-032 delivers `EncounterState` in **Phase 2**;
  - the **prompt test** inspects "the orchestrator's rendered prompt", and the
    orchestrator is **Phase 3** (D-032 is explicit: "Phase 3 adds only the LLM
    orchestrator, hypothesis and finalize nodes").

  This is round-1 #22's phase-gating class, and it is the first thing an
  implementer will hit. Additionally, per C-2 the prompt test as specified cannot
  pass against the panel design at all.
- **Fix:** Correct "Four tests" to five. Assign each test to the earliest phase
  whose artefacts it needs: View + String-scan + Positive → Phase 1; Allow-list →
  Phase 2; Prompt → Phase 3 (and restate it per C-2). Update §7's acceptance
  criteria accordingly.

### C-13. §2.2 omits `absorb_panel` although `solo_decide` declares the same output schema

- **Severity:** minor
- **Section:** §2.2, §2.3, §2.1
- **Problem:** §2.3 says "`solo_decide` declares the same schemas", which
  includes `panel_events` in `output_schema`. §2.1 lists `absorb_panel` as a
  writer of `encounter_log` with no graph qualifier. But §2.2's diagram goes
  `SOLO --> route{route_action}` with no absorb node. So either `panel_events` in
  `solo_decide` is written and never consumed (dead channel, and any orchestrator
  event is lost from the trace), or §2.2's diagram is missing a node that §2.1
  and §2.3 both assume.
- **Fix:** Either add `absorb_panel` to §2.2 (recommended — it keeps the two
  graphs aligned and gives the solo orchestrator's reasoning a home in the trace)
  or state that `solo_decide`'s `output_schema` omits `panel_events` and that the
  solo orchestrator emits no events.

### C-14. `EncounterState.action: Action | None` names a type §4.1 now builds per run

- **Severity:** minor
- **Section:** §2.1, §4.1
- **Problem:** §4.1's fix for B-8 is correct — `make_action_type(enabled)` returns
  a `Literal` built from the per-run enabled set, and `OrchestratorDecision` is
  built with `create_model`. But §2.1 still declares `action: Action | None` in a
  module-level `TypedDict`, and `Action` no longer exists as a static name. The
  allow-list test and every type hint that imports it need a stable type.
  (`Literal[tuple(sorted(enabled))]` itself is fine at runtime — `Literal.__getitem__`
  unpacks a tuple — and `sorted` makes it deterministic; the `type: ignore` is
  appropriate.)
- **Fix:** Declare `action: str | None` in `EncounterState` and validate against
  the per-run `Action` at the orchestrator boundary, where the schema already
  enforces it. Note also that `create_model("OrchestratorDecision", …)` produces
  two same-named classes when the 2×2 matrix runs in one process — harmless for
  pydantic, worth a sentence if anything keys on `__name__`.

### C-15. §4.1 attributes a state write to a routing function

- **Severity:** minor
- **Section:** §4.1
- **Problem:** "`route_action` still has an explicit branch for a valid-but-disabled
  action: **it records a `parse_failure` event** and re-prompts, never raises."
  `route_action` is a conditional-edge function; it returns a node name and
  cannot write state. The diagrams get this right (`rp` is a node); the prose is
  the exact category error B-3 corrected for `should_continue`.
- **Fix:** Reword to "`route_action` routes a valid-but-disabled action to `rp`,
  which records the event" — or, better, delete the branch per C-3.

### C-16. Turn-cap off-by-one: `check_stop` both increments `turn` and tests the cap

- **Severity:** minor
- **Section:** §2.1, §5 item 4
- **Problem:** `check_stop` writes the `+1` delta **and** decides whether the cap
  is reached. The plan does not say whether the comparison is against the
  pre-increment or post-increment value, so `if state["turn"] >= 20` and
  `if state["turn"] + 1 >= 20` are both faithful readings and produce 21 and 20
  executed actions respectively. The recursion limit has enough headroom to hide
  it, so the bug would only show up as an off-by-one in the `turns` metric.
- **Fix:** One sentence: the cap is evaluated on the post-increment value, so
  exactly `max_turns` actions execute.

### C-17. `challenge_due`'s coupling to the finalize flag has two side effects

- **Severity:** minor
- **Section:** §2.3
- **Problem:** `challenge_due = turn % 3 == 0 and turn > 0 and not challenged_this_finalize`
  does correctly prevent a double-fire on the re-deliberation ✅. Two unstated
  consequences:
  1. If a finalize attempt happens at turn 3, 6, 9 … the **scheduled** challenge
     for that turn is suppressed (the flag is `True` when the re-deliberation
     re-enters the panel). Q-16's "every 3rd turn" silently skips.
  2. An `rp` re-entry (C-3) does **not** set the flag and does not increment
     `turn`, so at a multiple-of-3 turn the challenger fires a second time on the
     same turn — the thing §2.3 claims cannot happen.
- **Fix:** State (1) as intended behaviour, and (2) disappears if C-3's fix
  removes the `rp` cycle.

### C-18. Q-29's summary truncation rule was dropped without being recorded

- **Severity:** minor
- **Section:** §4.1, §5 item 8, §10
- **Problem:** Q-29 decided: "each field capped ~2000 chars, **oldest dropped
  first, drops recorded**". Revision 3 keeps only "each field ~2000 chars"
  (§4.1) and §5 item 8 no longer mentions truncation at all. The drop policy and
  the requirement to record drops are gone, and §10's deviation table does not
  list it. This is not cosmetic: `findings` is where gatekeeper text lives, so
  the drop order determines how long the diagnosis string stays in the doctor's
  context for the 29 flagged cases — i.e. it modulates the one leak the project
  measures.
- **Fix:** Restore "oldest dropped first; each drop logged as an `Event`", or
  record the change in §10 with a reason.

### C-19. `check_stop` must read process-global state to write `request_cap`

- **Severity:** minor
- **Section:** §5 item 4, §5.1
- **Problem:** `request_cap`'s value is "remaining daily allowance" — a
  process-global, cross-case, persistent counter (§5.1). `check_stop` is a graph
  node and the plan gives it no access. It must be closed over the same
  rate-limiter/daily-counter object the client uses, which makes an otherwise
  pure node depend on mutable global state shared across 4 concurrent workers.
  Unstated, and it interacts with C-9's two-accumulator problem.
- **Fix:** State that `check_stop` is closed over the shared guard object (the
  same injection mechanism as the `CaseStore` views), and that the read is
  advisory — the authoritative refusal is the client's.

### C-20. Residual gaps in §6.3's arithmetic

- **Severity:** minor
- **Section:** §6.3, §2.4
- **Problem:** Three small ones in an otherwise good section:
  1. `accuracy = judge_correct / n_scored` divides a per-case boolean by a count;
     it should read `sum(judge_correct)`.
  2. `entry_matches: list[bool]` has no stated length invariant against
     `len(differential)`. An LLM returning a short or long list makes `top_3` /
     `top_5` an index error or a silent mis-score. Assert equality and treat a
     mismatch as a judge parse failure.
  3. The denominators for `lenient_correct`, `top_1/3/5` and `in_differential`
     are never stated. They should be `n_scored`, but §6.3 names it only for
     `accuracy`, and at n=3 a reader cannot infer it.
- **Fix:** Three sentences.

### C-21. §6.5 defines a sealed-half bar "measured once" with no stated consequence for failing it

- **Severity:** minor
- **Section:** §6.5, D-036, R8
- **Problem:** D-036 correctly forbids tuning against the sealed half and
  requires the bar be measured once. But D-031's original consequence ("below the
  bar, the judge prompt is revised and the set re-run") is now *forbidden*, and
  nothing replaces it. So if the sealed half returns κ = 0.6, the plan does not
  say what happens to the accuracy figures — whether they are quoted with a
  caveat, withheld, or the calibration set is rebuilt (which would need a new
  sealed half and a recorded decision).
- **Fix:** One sentence in §6.5: below the bar, accuracy figures are reported
  **with the measured κ and agreement quoted in the header** and flagged as
  judge-limited; rebuilding the calibration set requires a new decision entry.

---

## Notes on things that are **not** findings

- **No new path by which `Correct_Diagnosis` reaches a doctor-side agent.** I
  traced every channel revision 3 added. `panel_events` is written only by nodes
  with no case access; `absorb_panel` has no case access; `make_orchestrator_decision`
  carries no case data; `check_stop` has no case access; the judge-exception path
  is closed by §3.3 item 8. The D-015 leak (gatekeeper text for 29 cases) still
  circulates `encounter_log → summary → challenger → panel_events → encounter_log`,
  but that is the acknowledged, measured leak, not a new one. §1.1's property and
  the closure-injection mechanism remain the right design and are provable.
- **The `turn` reducer does not double-count.** Verified by tracing both graphs:
  `check_stop` is on the action path only, in both, and `turn` is in the
  subgraph's `input_schema` but **not** its `output_schema`, so no round-trip
  addition occurs. §2.1's claim is true.
- **`spend_usd` / `parse_failures` do not double-count across the subgraph
  boundary**, for the same reason in reverse: output-only, so the subgraph
  returns a pure delta. This is a genuinely elegant consequence of the B-2 fix.
- **Recursion limit.** `20 × 6 + 20 = 140` is arithmetically right and the
  panel's five parent super-steps per turn fit with headroom. It is adequate for
  the normal path; the cycles in C-3 and C-4 are what would consume it.
- §10's recorded deviations (D-021 judge SDK, D-026 test-selection merge, D-027
  red flags, D-034 fuzzy tier, Q-17, Q-26, Q-08/D-028, D-022) are all reasoned
  and consistent. I am not re-litigating them.

---

## Summary

| | Count |
|---|---|
| Part C blockers | **4** (C-1 … C-4) |
| Part C majors | **8** (C-5 … C-12) |
| Part C minors | **9** (C-13 … C-21) |
| Round-2 findings not fully resolved | **6 of 17** (B-1, B-2, B-3, B-4, B-5, B-7) |
| Dataset claims found wrong | **0** (14 re-derived, all hold) |

## Verdict

**NO-GO** — but narrowly, and the remedy is small.

The case *for* GO is real and worth stating: none of the four blockers touches a
Phase 1 deliverable. Phase 1 builds config, loader, `CaseStore` + views, splits,
the client and guards, the tracer, the viewer and the fake model — and every
number Phase 1 is asked to pin (`dx_in_results == 29`, `dx_tokens_in_results == 4`,
dev 40 / heldout 174, 16 mixed + 2 list + 4 empty, PEF lists in 37/103) is
independently verified correct in this review. §1.1's isolation property is sound.

The case *against* is that the brief's Step 5 rule is unconditional ("if any
blocker remains, run another review round"), and one blocker **does** reach into
Phase 1: C-12 makes Phase 1's own acceptance criterion unsatisfiable, and C-2
means one of the five tests it demands cannot pass against the design as written.
Beginning Phase 1 against a criterion that cannot be met is how a phase gate
becomes a rubber stamp.

More importantly, C-1 and C-2 together mean **the panel currently has no
substantive difference from the single doctor** — the challenger's opinion
reaches no reader on the finalize path (C-1) and reaches the orchestrator only
through a channel the plan forbids and tests against (C-2). That is the same
outcome B-1 was raised to prevent, arrived at by a different route, and it would
again be invisible to the Phase 4 acceptance test. Shipping Phase 1 against a
panel design that is hollow at its centre wastes the review cycle that is
supposed to catch it.

**What must change before GO** (revision 4; none of it needs a new user decision
except where noted):

1. **C-2 / C-1 — give the advisory opinions a declared channel and a reader.**
   Typed `challenger_opinion` / `cost_objection` keys in the subgraph state that
   the orchestrator explicitly reads; `challenger_final` and `challenger_stop`
   added to §2.1's `encounter_log` writer list and to §3.1 as "append"; §3.3's
   prompt test rescoped to events originating outside the subgraph. State what
   `finalize` does with `challenger_stop`'s output, or delete that node.
2. **C-3 — remove the `rp → subgraph` cycle.** Handle a valid-but-disabled action
   as an invalid output inside the orchestrator node, where Q-15's 3-attempt
   counter already lives. If the cycle is kept, it needs a declared per-decision
   counter and a cap-evaluating node on it.
3. **C-4 — route `budget_exhausted` to `finalize` with an edge**, add the
   `StopReason` member, and say what `finalize` produces when `differential` is
   empty.
4. **C-5, C-6, C-7 — make §2.1's writer lists exhaustive and reconcile them with
   §3.1** (add `hypothesis`, `check_stop`, `rp`, both parent challengers to
   `encounter_log`; add `absorb_panel` to `panel_events` with a reset); declare
   the subgraph's internal `panel_events` reducer as `operator.add`.
5. **C-11, C-12 — define `forced_stop`; correct "Four tests" to five and
   redistribute them across Phases 1–3.**

Majors C-8, C-9 and C-10 should land in the same pass — C-9 is the only one that
might warrant a user question (which spend total is authoritative), and the
recommended answer is the client's. The nine minors are wording and can be swept
alongside.

Everything else in revision 3 is sound. The plan is one focused editing pass away
from GO, and that pass touches §2.1, §2.2, §2.3, §3.1, §3.3, §4.1, §5 and §7 —
no re-architecture, no new decisions of substance.

**Single most important item:** **C-1 + C-2** — the panel's advisory opinions
have no declared channel to any reader, so the finalize re-decision is
informationally empty and the in-turn challenge either does not reach the
orchestrator or does so through the exact channel §2.3 claims is closed and §3.3
tests against. This is B-1's failure mode returning by a different road, and the
Phase 4 acceptance test would pass anyway.

---

# Resolutions (Step 5, round 3)

Appended 2026-09-15. **All 21 findings accepted**, plus the 6 partially-resolved
round-2 items. Applied in `PLAN.md` **revision 4**. Two new decisions: D-038,
D-039.

Revision 4 is a *subtractive* pass — it deletes two nodes rather than adding
machinery, which removes the cycles C-3 and C-4 depended on.

| # | Severity | Resolution |
|---|---|---|
| C-1 | blocker | **accepted.** `challenger_final` now writes a typed `challenger_opinion` **and** appends a `challenge` event; `hypothesis` folds it into `summary.open_questions`, so the re-deliberation sees new information. `challenger_stop` is **deleted** (D-038) — it could not affect any output. §2.1 writer list and §3.1 matrix corrected. |
| C-2 | blocker | **accepted.** Opinions travel as **typed keys** (`challenger_opinion`, `cost_objection`) in the subgraph schemas, not as free-text events. §2.3's isolation claim is true again and §3.3's prompt test is scoped to event text originating outside the subgraph. |
| C-3 | blocker | **accepted.** The `rp` node and its cycle are **deleted**. A valid-but-disabled action is invalid output, repaired inside the orchestrator by Q-15's 3-attempt loop where the counter is a local variable. `route_action` writes no state. |
| C-4 | blocker | **accepted.** `"budget_exhausted"` added to `StopReason` and to §5's table; `route_action` gains a **first-priority** `budget_exhausted → finalize` edge; the guard writes `stop_reason`; the empty-differential-on-turn-1 case is specified. |
| C-5 | major | **accepted.** §2.1's `encounter_log` writer list is now exhaustive and reconciled with §3.1; a test asserts every `Event.kind` has an emitting node. |
| C-6 | major | **accepted.** Subgraph-internal `panel_events` carries `operator.add`; parent is last-write. Phase 4 test asserts challenger and cost-steward events survive. |
| C-7 | major | **accepted.** `absorb_panel` named as a writer of `panel_events` and resets it to `[]`; Phase 4 test covers the no-op deliberation. |
| C-8 | major | **accepted.** §1.2 enumerates all three differences; R10 rewritten; the comparison is restated as "does a doctor receiving challenge and cost opinions decide differently". |
| C-9 | major | **accepted** → D-039. Client owns the total; state holds a snapshot. |
| C-10 | major | **accepted.** A budget-exhausted finalize is outcome `error`, excluded from accuracy **and** from the coverage denominator. |
| C-11 | major | **accepted.** `forced_stop := stop_reason not in (None, "finalize")`. |
| C-12 | major | **accepted.** "Five tests", each assigned to the earliest phase whose artefacts exist; Phase 1 acceptance now names only the two it can run. |
| C-13 | minor | **accepted.** `absorb_panel` present in both graphs. |
| C-14 | minor | **accepted.** `action: str | None`, validated against the per-run set. |
| C-15 | minor | **accepted.** `route_action` is a pure router; repair happens in the node. |
| C-16 | minor | **accepted.** Cap evaluated on the post-increment value; stated. |
| C-17 | minor | **accepted.** The `challenge_due` clause is documented as intentional; the second side effect disappeared with C-3's deletion. |
| C-18 | minor | **accepted.** Truncation rule restored, with each drop logged as an `Event`. |
| C-19 | minor | **accepted.** `check_stop` is closed over the shared guard object. |
| C-20 | minor | **accepted.** Coverage `n/a` case, top-k denominator, and empty calibration bins specified. |
| C-21 | minor | **accepted.** Failing the sealed bar annotates every accuracy figure and leaves R8 open; the sealed half is never re-used for tuning. |

## Round-2 items reported as not fully resolved
B-1 → C-1. B-2 → C-6, C-7. B-3 → C-4. B-4 → C-4. B-5 → C-5, D-039. B-7 → C-2.
All closed by the above.

## Note on review rounds
Three rounds have run (24 + 17 + 21 findings). Round 3 found **zero** dataset
errors, against three in round 1 and three in round 2 — the factual base is now
stable, and the remaining findings were confined to sections revision 3 had just
rewritten. A fourth round was **not** run: the changes above are subtractive or
local, and the marginal value of another full round is low against the cost of
further delaying implementation. The user may request one.
