# Sim review — 2026-09-27

What went wrong in the recorded runs, what was fixed, and what the first run
after the fixes showed. Decisions: D-056 to D-062 in `docs/DECISIONS.md`.

## How the review was done

Six analysts read every trace, final answer, report, prompt and the decision
log, each through a different lens; a consolidator merged 111 raw findings into
48. The per-finding verification fan-out was stopped at the user's request and
the findings were reviewed directly instead: the load-bearing claims were
re-checked against the traces, the case files and the code — the wrong-region
match by running the gatekeeper, the repeat guard by replaying it over the
recorded transcripts. Scope was approved as groups 1-6 (behaviour, patient,
gatekeeper, harness, observability), deferring evaluation-only items; the stop
signal was kept information-only.

## The glaring mistake, diagnosed

The doctor re-ordered refused tests **with D-055's marker in its summary at
every decision** (1491dcac t7, t10, t14; truncation ruled out). It also
re-ordered *fulfilled* tests — medqa-0009 has two, the doctor ordered nine —
and re-asked questions: HIV status or risk in 8 of 18 turns. Root cause: no
state recorded what the doctor had done, and nothing in code rejected a repeat.

## All 48 findings

| id | sev | finding | status | where |
|---|---|---|---|---|
| M-01 | critical | Refused tests are re-ordered even though the D-055 marker is in the orchestrator's summary at every re-order decision; n | fixed | D-056 repeat guard + ledger |
| M-02 | critical | Transport, auth and rate-limit errors (401/402/404/429/5xx) are handled as schema failures: re-prompted at once with the | fixed | D-059 failure classes |
| M-03 | high | A failed gatekeeper LLM-tier call (429, timeout, budget breach) becomes a fabricated, permanent 'not part of the case re | fixed | D-057 failures propagate |
| M-04 | high | The orchestrator never sees the D-055 refusal wording, no prompt says unavailability is permanent, and the finalize crit | fixed | D-056 ledger rendering, prompts |
| M-05 | high | The doctor re-asks questions already asked, including ones answered 'I don't know', because the summary keeps no mechani | fixed, partly | D-056 question guard + ledger; misses loose rephrasings (1491dcac t5, t18) |
| M-06 | high | The doctor keeps investigating long after the leading diagnosis is settled (78% of actions come after the MRI that names | fixed (info only) | D-060 progress signal, at the user's choice |
| M-07 | high | On turn_cap, check_stop goes straight to finalize without a hypothesis pass, so the last action's result never reaches t | fixed | D-059 hypothesis_final before a turn-cap finalize |
| M-08 | high | The gatekeeper serves an already-returned record again for more specific requests, and the doctor keeps re-ordering, con | fixed | D-057 'same record' reply + D-056 guard |
| M-09 | high | The gatekeeper synonym tier returns the wrong region: 'MRI spine' returns MRI_Brain, whose result names PML | fixed | D-057 |
| M-10 | high | A structured-output or provider failure in hypothesis or ask_patient crashes the whole case; only the orchestrator, fina | fixed | D-059 harness guard -> provider_error |
| M-11 | high | A text reply instead of the forced tool call makes structured() return None with no repair: four nodes crash and finaliz | fixed | D-059 NoToolCall |
| M-12 | high | Empty-response detection is dead under the configured function_calling mechanism: null choices and in-body errors get an | fixed | D-059 classify() |
| M-13 | high | Rate limits get no backoff and no shared cooldown; even the transient path's backoff caps at 16 s, far too short for an  | fixed | D-059 backoff + shared cooldown |
| M-14 | high | Token and cost usage comes from one shared mutable UsageRecorder.last: failed calls replay the previous call's usage and | fixed | D-059 usage per call |
| M-15 | high | DeepSeek switch: the Novita pin with fallbacks off will fail every call unless re-pinned, nothing requires the endpoint  | fixed, partly | D-061 probe + Together pin; served provider not recorded (pinned, no fallbacks) |
| M-16 | high | No max_tokens and no reasoning control on any agent request: runaway 32k-39k-token generations, 120 s timeouts, and comp | fixed | D-061 role_settings |
| M-17 | high | No credential or endpoint preflight: the web viewer and the CLI start runs with a dead key and burn quota finding out | fixed | D-062 preflight incl. routed call |
| M-18 | high | The doctor's decision state is never persisted: the orchestrator's reason and expected_information are thrown away, and  | fixed | decision + assessment trace records |
| M-19 | high | The web UI mislabels failures live, replays crashed or dead-key runs as 'Finished', and a failed web run writes no summa | fixed | summary.json on every exit; client messages |
| M-20 | high | Panel: the challenger's typed opinion and D-051 guidance go to the orchestrator, which cannot rank; its untyped text rea | fixed | D-058 evidence-only transcript |
| M-21 | high | The patient is stateless across turns, so it contradicts its own earlier answers and invents details; patient.md's 'if a | fixed | D-058 patient history |
| M-22 | high | The patient claims not to know a fact that is in the case file (she is still on natalizumab), 3 times, and the error rea | fixed, unverified live | D-058 patient prompt |
| M-23 | medium | gatekeeper.normalise does not drop punctuation despite its docstring, so real keys miss the deterministic tiers and toke | fixed | D-057 normalise |
| M-24 | medium | The doctor almost never examines the patient (1 exam in 69 recorded actions); the hypothesis prompt says it cannot, and  | fixed | hypothesis.md no longer says 'cannot examine'; 1 exam in 9 sim actions |
| M-25 | medium | After the diagnosis is set, the orchestrator spends turns on treatment-management and low-value questions, and tells the | fixed | orchestrator.md: the job ends at a diagnosis |
| M-26 | medium | About a third of the hypothesis transcript is the doctor's own earlier output and harness bookkeeping, labelled as the d | fixed | D-058 |
| M-27 | medium | Bundled test requests are silently half-filled, but the summary shows them as fully done ('MRI brain and spinal cord' ma | fixed, heuristic | partial marker; one false positive in the sim (breast exam) |
| M-28 | medium | Asked about her medical history, the patient left out Crohn disease and natalizumab | fixed, verified live | D-058; medqa-0002 gave full history in the sim |
| M-29 | medium | Final answers treat refused or never-returned investigations as pending and assert evidence never obtained, because fina | fixed | finalize.md: nothing is pending |
| M-30 | medium | The challenger must always name a dangerous unexcluded alternative and is not told that tests missing from the record ca | fixed | challenger.md |
| M-31 | medium | Final diagnoses carry cause and grade qualifiers that sit on the strict judge's 'narrower = wrong' boundary, and there i | fixed, partly | finalize.md format rule; structural split deferred |
| M-32 | medium | Test prices use an exact key lookup: MRI_Brain costs the $90 median instead of $1400, every exam costs $90, refused orde | deferred | evaluation-only (price lookup) |
| M-33 | medium | The per-role model assignment in models.yaml has no effect: one chat model, built from the orchestrator entry, serves ev | fixed | D-061 per-role models |
| M-34 | medium | Free-tier throttles (18 requests/min shared bucket, 1000/day cap, pre-flight refusal) still apply to a paid model, and t | fixed | D-062 |
| M-35 | medium | The web server shares one SpendTracker across all runs, so the $0.50 per-case cap becomes a lifetime cap per case id (De | fixed | D-062 spend per run |
| M-36 | medium | 17 calls hit the 120 s hard timeout (2,040 s lost), each can be retried up to 4 times, and nothing detects a stall early | fixed, partly | timeout retries capped at 2; no time-to-first-token deadline |
| M-37 | medium | The automated judge has never produced a verdict (0 in 9 attempts across 5 eval runs); its credentials are checked only  | deferred | evaluation-only (judge preflight) |
| M-38 | medium | The eval runner throws away the transcript and every behaviour counter when a case crashes or hits its deadline (D-054 o | fixed | runner streams |
| M-39 | medium | Failure records mislabel what happened: timeouts are logged as 'empty_response', causes are truncated, and reports count | fixed | distinct failure events |
| M-40 | medium | Call records cannot answer basic questions: whether a call failed, which turn it was, reasoning tokens, provider, trunca | fixed, partly | status/attempt/reasoning/finish_reason/model/id recorded; no turn, no served provider |
| M-41 | medium | The D-055 fix was verified by checking that the marker is present, not by checking behaviour, and no report metric count | fixed | redundancy metrics in results + report |
| M-42 | low | Red flags are unreliable: finalize invents their turns (all 22 say turn 1), flags vanish from state, and red-flag events | deferred | evaluation-only (red-flag turns); duplicate red-flag events fixed |
| M-43 | low | Summary truncation drops the oldest entries first, which would remove the earliest refusals and first results, and the o | fixed | ledger never truncated |
| M-44 | low | A single `unknown` flag on multi-part answers makes patient_unknown_rate unreliable | deferred | evaluation-only (unknown flag) |
| M-45 | low | orchestrator.md lists search_literature, which is disabled | fixed | action list rendered from enabled set |
| M-46 | low | The judge prompt's examples are verbatim dataset ground truths, one from a held-out case | deferred | evaluation-only (judge examples) |
| M-47 | low | The report's per-case table prints 0 and '—' for telemetry that was not recovered | fixed | per-case n/a |
| M-48 | low | All live transcript evidence comes from medqa-0002, a case whose MRI result names the diagnosis | addressed | sim ran all three cases |

## The first sim after the fixes

`runs/dev-single_doctor-000eec7f` — DeepSeek v4.1 Flash on Together, the same
three cases and configuration as the baseline `dev-single_doctor-39b1d0c4`.

| | Baseline (ling, free) | After (DeepSeek + fixes) |
|---|---|---|
| Correct (hand-judged) | 3/3 | 3/3 |
| Actions | 37 (13 / 20 / 4) | 9 (3 / 4 / 2) |
| Turn-cap stops | 1 | 0 |
| Tests ordered | 16 | 5 |
| Refused / re-delivered orders | 3 / at least 7 | 1 / 0 |
| Provider failures | several | 0 |
| Cost | $0 | $0.027 |

**What it does not show.** The model and the code changed together, so the
run cannot attribute the improvement to either. The repeat guard never fired —
DeepSeek did not try to repeat itself — so its evidence remains the replay on
the recorded ling transcripts: 12 of 32 actions blocked, every one a genuine
repeat. n=3, dev set, hand verdicts, and medqa-0002's MRI names its diagnosis.

**What it surfaced.** Asked whether she still has periods, the medqa-0009
patient said she could not answer: Q-10's split has no category for a personal
fact with no natural negative, and any concrete answer would invent a finding.
Open for a decision. The partial-request marker also misfired once, on a
breast-and-axillary exam whose record did cover the axilla.

## Lessons carried forward

- **Showing the model a marker does not enforce a rule; D-055 did not stop repeat orders.** D-055 added a clarified refusal and a '[no result: not in this case]' marker in summary.tests_ordered. At turn 10 of 1491dcac the orchestrator re-ordered 'CSF JC virus PCR', byte-identical to a marked line it had just been shown, and c79bb4e6 repeated the same order at t4 and t6. Only a code-level rejection of the decision will hold. *(D-055; web-single_doctor-1491dcac t4/t7/t10 and t6/t14; web-single_doctor-c79bb4e6 t4/t6; disproven)*
- **A test that proves the fix is rendered does not prove the behaviour changed.** D-055's test checks that the marker appears in the orchestrator's summary, and it passes while the behaviour persists. This is the same trap as Phase 4 §4.1, where the suite covered the half of D-025 that worked. *(D-055 tests; PHASE_4_NOTES §4.1; verified live)*
- **A summary-only doctor has no memory of what it already asked.** Q-29 and D-041 made findings model-authored and kept only test requests mechanical. The orchestrator therefore asked about HIV status 8 times in 18 turns and about natalizumab discontinuation 3 times in a row. *(Q-29; D-041; 1491dcac; 1d3154ff; open)*
- **The dataset leaks answers, and all live transcripts so far are from a leaked case.** In 29 of 214 cases the diagnosis string appears in Test_Results. The medqa-0002 MRI result names PML, and every web run led with PML from turn 1-3 (sometimes before the MRI, from the natalizumab history). *(D-015; PHASE_0_NOTES D1; web runs; verified live)*
- **Check model capabilities on the schema you actually use; advertised support is not working support.** Nemotron passed a flat schema but failed 0/2 on the nested HypothesisUpdate and refused 46% of calls in a full encounter. The deepseek switch needs the same spike on the real nested schemas. *(PHASE_3_NOTES §7.1; D-043; verified live)*
- **A small spike does not establish reliability under load.** D-043 chose ling after 'zero backoffs' across one encounter. Later runs had 3 timeouts (1491dcac), 9 (d14c436b) and upstream 429s that crashed c79bb4e6. *(D-043; 1491dcac; d14c436b; c79bb4e6; disproven)*
- **When a provider returns nothing, back off and resend the same prompt; do not re-prompt.** HTTP 200 with choices:null returns in about 0.3 s. Re-prompting it crashed aab6b8b1; after the fix, empty responses and timeouts back off and recover (1491dcac recovered 3 times). *(PHASE_3_NOTES §7.3; aab6b8b1; 1491dcac; verified live)*
- **Removing a retry layer means every error it handled needs a new owner.** D-044 set max_retries=0 and said the repair loop owns retries, but the loop only backs off on empty responses and timeouts. c79bb4e6 re-prompted a 429 three times in 0.9 s and crashed, and a 401 was re-prompted 3 times. *(D-044 item 2; c79bb4e6; web-panel-8c0fe568; disproven)*
- **Per-operation httpx timeouts do not bound wall-clock time; only a total deadline does.** A trickling stream hung a run for 65 minutes. After D-044 every stuck call ends at about 120.0 s and the loop recovers. *(D-044; PHASE_3_NOTES §7.4; 97f8b850; 1491dcac; verified live)*
- **Diagnose a hang from socket state and a stack sample, not from the process list.** The first two guesses (a retry storm, a dead process) were wrong; lsof plus sample identified the trickling stream. *(PHASE_3_NOTES §7.4; verified live)*
- **Pass callbacks per invocation, or usage records silently read zero.** with_config(callbacks) followed by with_structured_output drops the callbacks. Traces have carried real token counts since the fix. *(PHASE_3_NOTES §7.2; verified live)*
- **Usage passed through one shared mutable slot is misattributed.** Failed attempts re-record the previous call's usage, and one recorder shared across concurrent cases mixes them: identical usage appears in medqa-0002 and medqa-0009 at 16:57:37.156 in 39b1d0c4. This was harmless at $0 cost and will distort spend on a paid model. *(PHASE_3_REVIEW #5(b); 39b1d0c4; 97f8b850; d14c436b; 1491dcac; open)*
- **OpenRouter reports usage.cost as 0 for :free models, so the spend cap has never been exercised.** Every llm_call so far has cost 0.0. The per-case cap and D-039's spend authority are untested against real costs. *(PHASE_1_NOTES §6; PHASE_3_NOTES §7.1; verified live)*
- **Contain judge failures per case and write the expensive half first.** All 7 automated judge attempts failed on auth, yet every run kept its finals and results. The runs were then re-judged by hand via --verdicts. *(PHASE_3_REVIEW #2; D-047; judge.jsonl of 5 runs; verified live)*
- **There is no route to an automated judge yet.** There is no subscription path for inference through the SDK. Every accuracy figure so far is hand-assigned, and D-036 calibration and R8 remain open. *(D-021 correction; D-046; D-047; PLAN R8; verified live)*
- **A run must be recoverable from finals.json, but a recovered row must not present its gaps as zeros.** d14c436b was rebuilt after a NameError in report generation. The Behaviour section prints n/a, but the per-case table still prints 0 unlisted and '—' stop. *(D-050; PHASE_4_NOTES §4.3-4.4; d14c436b report.md; verified live)*
- **Encounter events must be persisted; the transcript is what exposes behaviour bugs.** The repeated-test bug went unnoticed in the reports (39b1d0c4 had 3 unlisted of 4 tests) until the web viewer showed the transcript. D-054 persists events on the web path, but the eval runner persists them only when a case succeeds. *(D-054; D-055 rationale; runner.py:141-156; verified live)*
- **Token containment makes the deterministic gatekeeper tiers actually fire.** Before D-045, 0 of 17 requests matched deterministically. Afterwards, every web-run MRI request matched MRI_Brain via tier 'contains'. Punctuated phrasings still miss because normalise keeps punctuation (offline probe). *(D-045; 39b1d0c4; web runs; verified live)*
