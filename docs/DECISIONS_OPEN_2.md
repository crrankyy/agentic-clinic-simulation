# Open decisions — round 2 (from the adversarial review)

> **STATUS: RESOLVED 2026-09-15.** All nine recommendations accepted. The
> authoritative record is `docs/DECISIONS.md` D-025 … D-033. Retained for the
> option analysis behind each choice.

Nine questions the adversarial review (`docs/PLAN_REVIEW.md`) raised that I must
not resolve myself. Everything else in that review — 16 of 24 findings — is
already accepted and folded into `docs/PLAN.md`.

**How to answer:** same as round 1 — *"all recommendations except Q2-0n"* is
enough. Answers become D-025 onward in `docs/DECISIONS.md`.

**Until these are answered, 4 blockers remain open and implementation cannot
start** (brief §1 Step 5).

---

## Q2-01 — What authority do the challenger and cost-steward have? *(blocker, review #3)*

**Why it matters:** the plan gave the challenger power to reopen a finalized
encounter and the cost-steward power to veto a test order. Neither traces to any
decision — brief §5.3 only says they "argue against" and "object to". The plan's
own schema can't even express the veto, and the reopen power created an
unbounded loop (review #1).

| # | Option | Trade-off |
|---|---|---|
| a | **Advisory only.** Both write opinions into `encounter_log`; the orchestrator gets exactly one re-decision after `challenger_final`, guarded by a `challenged_this_finalize` flag reset whenever a non-finalize action runs | Matches the brief's wording exactly. Bounded by construction — one extra call, no reopen counter, the review #1 loop class disappears. The panel effect is still real because the orchestrator sees both opinions before deciding. Costs one orchestrator call per finalize |
| b | **Executive.** Challenger may reopen once (`reopen_count` guard); cost-steward may replace the test with another action (`replacement_action` field) | Strongest panel-vs-single effect, which is the project's headline comparison. More control-flow machinery, two new state keys, and the sub-roles stop being "opinions" and become routers |
| c | **Split.** Challenger advisory; cost-steward may veto (binary block, no replacement) | Cost-steward's objection has teeth where money is spent; challenger stays faithful to the brief. Asymmetric authority needs explaining, and a bare veto with no replacement leaves the orchestrator to re-decide anyway — which is option (a) with extra steps |

**Recommendation:** (a). It is the minimal reading of the brief, it eliminates an
entire class of loop bug, and the panel still differs from the single doctor in
substance. Note it makes the panel/single comparison "does seeing challenge and
cost opinions change decisions", which is a cleaner question than "do veto rules
change outcomes".

**Answer:**

---

## Q2-02 — Should Test-selection be its own node? *(blocker, review #4)*

**Why it matters:** brief §5.3 lists **four** sub-roles — Hypothesis,
Test-selection, Challenger, Cost-steward — plus a separate orchestrator. The
plan silently folded Test-selection into `OrchestratorDecision.expected_information`
and then described `single_doctor` as having "no sub-roles" while its diagram
shows two of them. No decision authorises either.

| # | Option | Trade-off |
|---|---|---|
| a | **Merge, and correct the plan's wording.** Record that Test-selection lives in the orchestrator's `expected_information` field; restate §1.2 as "`single_doctor` = orchestrator + hypothesis + finalize; the panel adds challenger and cost-steward" | Saves ~20 LLM calls per case — about 25% of panel cost, which is material against a 1,000/day cap. The orchestrator already produces exactly what the brief asks Test-selection for. Deviates from the brief's node list, and Phase 4's "four sub-roles" becomes "two additional sub-roles" |
| b | **Add a real `test_selection` node.** Orchestrator becomes a pure router over its proposal | Faithful to the brief; each sub-role is separately inspectable, which serves the learning goal. One more call per panel turn (~25% more requests), and the orchestrator/test-selection split needs a clear responsibility boundary or they duplicate each other |

**Recommendation:** (a), because request budget is the binding constraint
(review #13) and the merged design already produces the brief's required output.
This is close, though — (b) is more faithful and more legible, and if you care
more about the learning goal than the daily cap, take (b).

**Answer:**

---

## Q2-03 — Rule-based red-flag detection: where, and with what thresholds? *(blocker, review #5)*

**Why it matters:** Q-28 chose "model-reported **plus** rule-based on
`Vital_Signs`" but no thresholds were ever defined, and the plan never said
where the check runs. Verified: `Vital_Signs` is free text in ~16 formats —
`'125/80 mmHg'`, `'Within normal range'`, `'Normothermic'`, `'Normal for age'`,
`'120 bpm (normal for age)'`, `'36.6°C (97.9°F)'`. Case 3 is a child with HR 120
annotated "normal for age"; a naive adult threshold flags it wrongly. Worse, if
the check runs *inside* the encounter it must read `Physical_Examination_Findings`
and write to doctor-visible state — handing the doctor vitals it never ordered,
violating brief §5.2.

| # | Option | Trade-off |
|---|---|---|
| a | **Drop the rule-based arm; model-reported only** (Q-28's original option (a)) | Removes a clinical-rule design task the brief never asked for and the plan cannot safely specify. No leak, no parser, no thresholds. Loses the comparator ("the panel flagged 12 of the 19 flaggable cases"), so red-flag rate becomes unfalsifiable |
| b | **Offline in the eval runner**, over gatekeeper results the doctor actually received | No leak — it reads only what was already returned. Gives a real comparator. Still needs the free-text parser and age-aware thresholds, which is genuine clinical-rule work |
| c | **Inside the encounter as a node** | What Q-28 arguably implied. Leaks unordered vital signs into doctor-visible state, contradicting brief §5.2 and the plan's own visibility matrix. I do not recommend this under any weighting |

**Recommendation:** (a). Building an age-aware free-text vitals parser is
exactly the over-engineering the brief warns against, and at n=3 the comparator
it would buy is worth nothing. If you want the comparator later, (b) is the
correct shape — it can be added without touching the encounter graph.

**Answer:**

---

## Q2-04 — How are abstentions scored? *(blocker, review #6)*

**Why it matters:** `abstain` and `parse_failure` both exist as columns but
nothing says how either affects headline accuracy. At n=3 a single abstention
moves accuracy by 33 points. Also undefined: what `FinalAnswer.diagnosis`
contains when abstaining, and whether the judge is called at all.

| # | Option | Trade-off |
|---|---|---|
| a | **Clinical abstention excluded from the denominator and reported as coverage; parse-failure excluded as `error` (consistent with Q-08); both counted explicitly.** `diagnosis=""` when abstaining and the judge is **not** called | Matches brief §9.3's accuracy-vs-coverage framing, keeps parse failures out of a clinical metric per Q-15, saves judge calls, and never asks the judge to score an empty string. Accuracy is then over a variable denominator, so `n_scored` must appear beside every figure — review #13 already requires that |
| b | **Abstention counts as incorrect** | One fixed denominator, simplest to read, impossible to game by abstaining. Punishes appropriate uncertainty, which is the opposite of what the Phase 6 abstention experiment is trying to study |
| c | **Clinical abstention excluded; parse-failure counts as incorrect** | Keeps the denominator closer to fixed. Scores a JSON formatting bug as a wrong diagnosis, which Q-08 explicitly rejected for API errors |

**Recommendation:** (a).

**Answer:**

---

## Q2-05 — The dev split contains no empty-`Test_Results` case *(blocker, review #7)*

**Why it matters:** D-022 requires the 3 evaluation cases to include one
empty-`Test_Results` case. **Verified as fact, not risk:** under seed 20260915
with grouped splitting, dev contains **zero** of the four empty-`Test_Results`
cases (69, 106, 111, 209). The constraint is unsatisfiable as written. Since
`splits.json` is committed once and never regenerated, this must be settled now.
(Dev does contain 5 `dx_in_results` cases, so that half is fine.)

| # | Option | Trade-off |
|---|---|---|
| a | **Drop the requirement; cover it with a unit test instead** | The empty-`Test_Results` path is *deterministic gatekeeper behaviour* — a unit test exercises it on all four cases, every run, for free and with no LLM. An expensive non-deterministic encounter proves strictly less. Leaves the split untouched. The 3-case run then never exercises the path end-to-end |
| b | **Force the empty-`Test_Results` group into dev before shuffling the rest** | Deterministic, documented, not seed-shopping; dev is *for* development so stacking it with edge cases is defensible. Slightly reduces dev's representativeness, and held-out loses one structural class |
| c | **Try seeds until dev contains one** | Smallest apparent change. It is seed-shopping on a permanent artefact, and sets a precedent I would rather not set in an evaluation project |

**Recommendation:** (a). A unit test is strictly better than an LLM encounter at
proving deterministic gatekeeper behaviour, and it costs nothing from the daily
request budget. (b) is a reasonable second if you want the end-to-end path
exercised.

**Answer:**

---

## Q2-06 — Provider pinning for the free model *(major, review #20)*

**Why it matters:** Q-05 decided `allow_fallbacks: false` with a provider
`order`, but no order was ever chosen. **Looked up live:** the model has exactly
**one** endpoint — provider `Nvidia`, 262k context, advertising `tools`,
`structured_outputs` and `response_format`. So there is nothing to choose
between; the only real question is what happens when that provider is down.

| # | Option | Trade-off |
|---|---|---|
| a | `order: ["Nvidia"]`, `allow_fallbacks: false`, **abort the run** on unavailability | Fully reproducible; a run either uses the intended model or does not happen. Loses a run to a transient outage, though the runner resumes from `results.csv` |
| b | `order: ["Nvidia"]`, `allow_fallbacks: false` for reported runs, fallbacks permitted for `--cache`/dev runs | Development is resilient, measurement is strict. Two behaviours to keep straight, though the run metadata records which applied |
| c | Allow fallbacks always, record the resolved provider | Most robust. This is Q-05's explicitly rejected option — detection after the fact rather than prevention |

**Recommendation:** (b).

**Answer:**

---

## Q2-07 — What is the judge calibrated against? *(major, review #21)*

**Why it matters:** Phase 5 promises an agreement report but never says what is
hand-labelled. Under D-022 only 3 encounters ever run, so there would be 3 judge
verdicts to agree about — not a validation. The judge produces the number every
other number depends on.

| # | Option | Trade-off |
|---|---|---|
| a | **Hand-label ~40 synthetic (final answer, ground truth) pairs** built from real `Correct_Diagnosis` values plus plausible near-misses — synonyms, broader/narrower terms, abbreviation variants, confident wrong answers | Validates the judge *now*, costs **zero** OpenRouter requests (the judge is on the Anthropic SDK), and deliberately covers the hard cases — which real 3-case output never would. You hand-label 40 pairs once. Validates on constructed rather than observed outputs |
| b | **Hand-label the judge's verdicts on 30–40 real dev encounters** | Validates on genuine model output. Costs 30–40 full encounters against the daily cap — roughly 2 days of budget — which is the constraint D-022 exists to avoid |
| c | **Defer calibration until evaluation scales beyond 3 cases** | No work now. R8's "judge is unvalidated" then stands indefinitely, and every reported number rests on it |

**Recommendation:** (a), with **exact agreement ≥ 90% or Cohen's κ ≥ 0.8** as the
bar, and a stated consequence: below the bar, the judge prompt is revised and the
set re-run before any accuracy figure is quoted.

**Answer:**

---

## Q2-08 — Phase 2's interactive mode needs Phase 3's orchestrator *(major, review #22)*

**Why it matters:** Q-31 put `interrupt()` at the orchestrator node, but the
orchestrator, `route_action` and `EncounterState` are all Phase 3 deliverables,
while interactive mode is Phase 2. As written Phase 2 either builds most of
Phase 3 or ships Q-31's explicitly rejected REPL.

| # | Option | Trade-off |
|---|---|---|
| a | **Pull the graph skeleton into Phase 2** — `EncounterState`, `route_action`, `should_continue`, and a human-driven orchestrator using `interrupt()`. Phase 3 then adds only the LLM orchestrator, hypothesis and finalize nodes | Honours Q-31's mechanism, and driving the real state machine by hand is an excellent way to find design faults before LLM non-determinism hides them. Phase 2 grows; Phase 3's acceptance criteria shrink correspondingly |
| b | **Non-graph REPL in Phase 2**, `interrupt()` deferred to Phase 3 | Keeps phase boundaries as written. This is Q-31's rejected option (b), and the REPL can mislead you about real graph behaviour |

**Recommendation:** (a), with the phase boundary restated so Phase 3 is
explicitly "add the LLM decision nodes to the Phase 2 skeleton".

**Answer:**

---

## Q2-09 — Free endpoints and the "nothing leaves the machine" claim *(minor, review #23)*

**Why it matters:** Q-32 rejected LangSmith on the rationale that nothing should
leave the machine, but every agent call now goes to a free OpenRouter endpoint.
**Checked:** OpenRouter's privacy docs confirm there are *separate data-policy
settings for free and paid models* on your account page, but do not state whether
free endpoints require allowing training. So this needs checking on your account,
not asserting.

| # | Option | Trade-off |
|---|---|---|
| a | **Check the setting, accept it, and correct the plan's wording** to "no LangSmith; note that free OpenRouter endpoints are governed by the account's free-model data policy, which may permit provider-side logging" | Honest. The data is public MIT-licensed benchmark text, not real patient data, so brief §2 is not violated either way. Requires you to look at the account setting once |
| b | **Disable training-permitting providers for free models** and accept that some free endpoints become unavailable | Strongest privacy posture. May make the chosen model unusable, since it has exactly one provider |
| c | **Ignore** — the data is public benchmark text | Nothing to do. Leaves the plan asserting a privacy property it may not have, which is the kind of quiet inaccuracy the review exists to catch |

**Recommendation:** (a).

**Answer:**

---

## Summary

| | Question | Severity |
|---|---|---|
| Q2-01 | Challenger / cost-steward authority | blocker |
| Q2-02 | Test-selection as its own node | blocker |
| Q2-03 | Rule-based red-flag detection | blocker |
| Q2-04 | Abstention scoring | blocker |
| Q2-05 | Dev split has no empty-`Test_Results` case | blocker |
| Q2-06 | Provider pinning | major |
| Q2-07 | Judge calibration set | major |
| Q2-08 | Phase 2 interactive scope | major |
| Q2-09 | Free-endpoint data policy | minor |

Q2-01, Q2-02 and Q2-03 are the ones worth real thought — they change the panel's
architecture and what the headline comparison measures. The rest have a clear
best answer.
