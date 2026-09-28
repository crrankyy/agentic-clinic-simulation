# 8. Testing

```bash
uv run pytest                    # 264 tests, ~20s, zero network access
uv run pytest -m live            # deselected by default; touches real APIs
```

**No test in the default run touches the network.** The preflight's HTTP calls
are answered by an `httpx.MockTransport`, and the web tests install a stub preflight. Graph tests drive
`FakeChatModel`, which replays a scripted list of structured outputs and records
`rendered_prompts` — which is how the isolation tests work: they assert on what a
node *actually sent*, not on what it was supposed to send.

| File | Tests | Subject |
|---|---|---|
| `test_sim_fixes.py` | 31 (+ parametrised) | The 2026-09-27 review: repeat guard, failure handling, gatekeeper, patient, preflight |
| `test_gatekeeper.py` | 19 | The matching cascade — the hardest non-LLM problem here |
| `test_panel.py` | 17 | Panel scheduling, advisory semantics, subgraph boundaries |
| `test_runner.py` | 16 | Outcome mapping, crash containment, judging, rebuild |
| `test_api.py` | 17 | The web surface, with isolation as the main subject |
| `test_download_dataset.py` | 14 | Checksums, atomicity, idempotency |
| `test_routing.py` | 13 | Every action, `RoutingError`, disabled-action repair |
| `test_single_doctor.py` | 13 | End-to-end encounters, caps, isolation |
| `test_metrics.py` | 9 | n=0 and n=1, Wilson interval, calibration |
| `test_leakage.py` | 11 | The leak rule, in **both** directions |
| `test_loader.py`, `test_splits.py` | 20 | Irregular shapes, determinism |
| `test_config.py` | 10 | Resolved configuration, derived values |
| `test_views.py` | 8 | Isolation at the view layer |
| `test_interactive.py` | 8 | `interrupt()` / resume |
| `test_guards.py` | 7 | Rate, daily, spend |
| `test_fake_model.py`, `test_patient.py` | 10 | Harness and patient behaviour |
| `test_state.py` | 5 | Reducers and the allow-list |
| `test_tracing.py` | 4 | Trace records, judge separation |
| `test_paths.py` | 3 | Repo-layout anchors |

## The isolation tests

Five, each assigned to the earliest phase in which its dependencies exist.

| Test | Asserts |
|---|---|
| **View test** | No doctor-side view exposes ground truth; `CaseStore` has no method returning a whole `Case` — checked **by reflection**, so adding one fails |
| **Allow-list test** | Every `EncounterState` key has a declared writer |
| **String-scan test** | Over leak-free cases, ground truth appears in no prompt **and no state channel** after a full scripted encounter |
| **Prompt test** | The orchestrator's rendered prompt contains no event text absent from the summary |
| **Positive test** | Ground truth **is** present in gatekeeper output for the 29 flagged cases |

### Why the positive test matters

It sounds backwards — asserting a leak. It is the reason a real bug was caught.

`json.dumps` defaults to `ensure_ascii=True`, which escaped non-ASCII characters
and made the string-scan test pass **falsely** for four non-ASCII diagnoses. A
one-directional test would still be green today. The positive test failed and
exposed it.

The same pattern recurs in `test_api.py`:
`test_reveal_is_the_only_route_that_returns_a_diagnosis` asserts the diagnosis
**is** on `/reveal` and **is not** on `/api/runs`. Without the positive half, a
scan that found nothing might mean the isolation works or might mean the scan
was looking for the wrong string.

## Tests written because a bug got past review

Each of these exists because something shipped, not because someone imagined it.

| Test | The bug |
|---|---|
| `test_a_cost_objection_reaches_the_next_orchestrator_decision` | `cost_objection` was in `PanelOutput` but not `PanelInput`; the objection was produced, carried out, cleared, and never read. **Half of D-025 silently did not happen**, and the tests were green because they covered the half that worked |
| `test_a_dangerous_alternative_reaches_the_orchestrator_with_its_likelihood` | The orchestrator promoted the challenger's most-*dangerous* alternative to most-*likely* — a rank inversion that lost `medqa-0012` |
| `test_a_rebuilt_run_does_not_report_unrecorded_telemetry_as_zero` | A rebuilt run printed dataclass defaults as measurements: `exams: 0`, `match tiers: (none)` for a run that ordered 25 tests |
| `test_starting_a_run_through_the_route_actually_schedules_it` | A sync FastAPI endpoint cannot `asyncio.create_task`; every start 500'd |
| `test_an_unavailable_test_is_marked_in_the_summary_the_orchestrator_reads` | A refused test rendered identically to a fulfilled one, so the doctor re-ordered what it could not have |
| `test_the_dataset_fixture_resolves_rather_than_skipping` | A moved path anchor turned **111 tests into skips** while the suite still reported success |
| `test_orchestrator_prompt_contains_no_raw_event_text` | The summary copied transcript text, so the orchestrator read the log *through* it |
| `test_a_refused_test_reordered_in_new_words_never_reaches_the_gatekeeper` | D-055's marker was in view at every re-order in 1491dcac; its test had checked only that the marker was rendered |
| `test_the_guard_replayed_on_1491dcac_blocks_only_repeats` | Pins the guard's behaviour on the real transcript: turns 7, 9, 10, 12, 13, 14, 16, 17 |
| `test_the_gatekeeper_never_returns_a_different_region` | "MRI spine" returned `MRI_Brain`, whose result names the diagnosis |
| `test_a_failed_model_call_is_never_turned_into_a_refusal` | Three 429s became "not part of the case record at all" 2 ms later |
| `test_a_revoked_key_fails_once_and_is_never_re_prompted` | A 401 was re-sent three times in 166 ms and reported as "did not validate" |
| `test_a_failed_call_does_not_recount_the_previous_calls_cost` | A shared usage slot re-billed the previous call on every failure |
| `test_the_last_answer_is_read_before_a_turn_cap_finalize` | On a cap, finalize never saw the last result |
| `test_every_result_field_survives_the_csv_and_rejudge_round_trip` | A re-judged report dropped every field added after the judge command was written |

## Negative controls

A test asserting a fix is worthless if it also passes without the fix. For the
load-bearing ones the fix was reverted and the test confirmed to fail — noted in
the docstring where it applies:

- `test_a_dangerous_alternative_reaches_the_orchestrator_with_its_likelihood`
- `test_an_unavailable_test_is_marked_in_the_summary_the_orchestrator_reads`
- every repeat-guard test (fail with `check_repeat` disabled)
- `test_the_gatekeeper_never_returns_a_different_region` (fails under the old synonym rule)

A lesson from D-055 sits behind this: a test that the fix is *rendered* does
not show the behaviour changed. The guard tests count gatekeeper calls and
turns spent, not strings.

## Conventions

- **`asyncio_mode = "auto"`** — async tests need no decorator.
- **`-m 'not live' --strict-markers`** — network tests are opt-in, and a typo in
  a marker name is an error rather than a silently-skipped test.
- Test names are **sentences stating the property**, not `test_foo_works`. A
  failure should read as a claim that has become false.
- Docstrings say **why the test exists**, and for regression tests, what shipped.
