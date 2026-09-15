# Phase 1 notes — foundations

**Status:** complete except one item that needs credentials (see *Pending*).
**Tests:** 75 passing, zero network access.

---

## 1. What was built

| Path | Purpose |
|---|---|
| `src/agentclinic/data/models.py` | `Case`, `PatientActor`, `Symptoms` — flexible enough for every irregular shape in the file |
| `src/agentclinic/data/leakage.py` | The `dx_in_results` rule and its weaker `dx_tokens_in_results` sibling |
| `src/agentclinic/data/loader.py` | JSONL → `Case`, stable IDs, fails loudly on anything malformed |
| `src/agentclinic/data/views.py` | `CaseStore` + the four views — the isolation mechanism |
| `src/agentclinic/data/splits.py` | Grouped split, canonical serialisation, eval-subset rule |
| `src/agentclinic/llm/guards.py` | Token bucket, persistent daily counter, authoritative spend tracker |
| `src/agentclinic/llm/openrouter.py` | `ChatOpenAI` → OpenRouter, usage callback, `LLMCaller` |
| `src/agentclinic/llm/fake.py` | Scripted `BaseChatModel` so graphs run offline |
| `src/agentclinic/tracing.py` | Per-case JSONL, judge records kept separate |
| `src/agentclinic/config.py` | Typed YAML loading with derived values |
| `src/agentclinic/cli.py` | `dataset`, `splits`, `config`, `trace` + the mandatory disclaimer |
| `config/*.yaml` | Models, budgets, illustrative test prices |
| `dataset/splits.json` | Written once, committed |

## 2. Run commands

```bash
uv sync
uv run pytest                                  # 75 tests, offline
uv run python -m agentclinic.cli dataset       # case counts and leakage flags
uv run python -m agentclinic.cli config        # resolved config incl. derived values
uv run python -m agentclinic.cli splits        # show the split (add --write once)
uv run python -m agentclinic.cli trace <run_id> <case_id>
```

## 3. Verification against the plan's predictions

Every number the plan predicted was reproduced by the implementation, not
asserted by hand:

| Prediction | Result |
|---|---|
| 214 cases load, IDs `medqa-0001`…`medqa-0214` | ✅ |
| `dx_in_results` == 29, leak-free denominator 185 | ✅ |
| `dx_tokens_in_results` == 4, on lines 13, 34, 123, 208 | ✅ |
| Diagnosis in **zero** `Physical_Examination_Findings` | ✅ |
| Empty `Test_Results` on 69, 106, 111, 209 | ✅ |
| 16 cases with top-level string values; lists in 153, 185; PEF lists in 37, 103 | ✅ |
| Undocumented `Patient_Actor` keys on 9 cases | ✅ |
| Split → dev 40 / heldout 174, no diagnosis spanning both | ✅ |
| 31 raw vs **35** case-insensitive duplicate groups | ✅ |
| Eval subset → `medqa-0002`, `medqa-0009`, `medqa-0012` | ✅ |
| Recursion limit derived as `max_turns * 6 + 20` | ✅ |

## 4. Surprises

### 4.1 Four diagnoses contain non-ASCII characters — and the first isolation test I wrote would have passed falsely because of it

`Hirschsprung’s disease` (14), `Glanzmann’s Thrombasthenia` (36),
`Legg-Calvé-Perthes disease (LCPD)` (104), `Crohn’s disease` (213).

The view test flattened views with `json.dumps(...)`, whose default
`ensure_ascii=True` renders `’` as `’`. The needle kept the literal
character, so a diagnosis containing a curly apostrophe **could never be found
in the haystack**. The positive test caught it — searching for a string that was
known to be present failed — but the same bug in the *negative* direction would
have silently weakened the project's central guarantee. Both the test helper and
`Tracer` now write with `ensure_ascii=False`, and there is a test for it.

This is the strongest argument yet for the positive leak test the round-1 review
asked for: it is the only test in the suite that fails when the search itself is
broken.

### 4.2 `Patient_Actor` text fields are not uniformly text

D-018 recorded irregular value types for `Test_Results`. The same is true of the
patient side, which no inspection had recorded: `Past_Medical_History` is a
string in 210 cases but a list in 1 and a dict in 2; `Social_History` is a dict
in 2; `Review_of_Systems` is a dict in **10**. `Medications` is a list in one
case and a string in another. D-018's principle ("model as optional, preserve
all") already covers it, so no new decision was needed, but a stricter model
would have rejected roughly a dozen cases.

### 4.3 The Phase 1 acceptance criterion was slightly wrong, and Phase 1 found it

`PLAN.md` claimed Phase 1 runs "the two Phase-1 isolation tests (view,
allow-list)". The allow-list test enumerates every `EncounterState` key, and
`EncounterState` is a Phase 2 deliverable under D-032. Corrected in the plan:
Phase 1 runs the view test; the allow-list test moves to Phase 2.

## 5. Decisions applied, with where to look

- **Q-01** IDs are line numbers → `loader._case_id`
- **Q-02** malformed records fail the load → `DatasetLoadError`
- **Q-09 / D-016** isolation by view + closure → `views.py`, and `CaseStore`
  deliberately has **no** method returning a `Case` (asserted by test)
- **D-015** leakage measured, not hidden → `leakage.py`, counts pinned
- **D-018** irregular shapes preserved → `models.py`
- **Q-36 / D-024** grouped, not stratified → `splits.py`
- **D-029** eval subset rule → `select_eval_subset`
- **D-039** the client owns the spend total → `SpendTracker`
- **Q-27** recursion limit derived, never constant → `config.load_budgets`
- **Q-13** unknown tests priced at the table median → `config.load_test_costs`
- **Q-06** `X-Title` only → `build_chat_model`
- **D-030** provider pinned → `config/models.yaml`

## 6. Pending — needs your action

**The Phase 1 live spike has not run.** `OPENROUTER_API_KEY` is not set, so two
things the plan requires confirming remain unconfirmed:

1. whether `nvidia/nemotron-3-super-120b-a12b:free` honours **strict structured
   output** in practice (it advertises `structured_outputs`, `response_format`
   and `tools` at its single endpoint, but advertised ≠ working); and
2. whether OpenRouter populates `usage.cost` for `:free` models.

Both are recorded as Phase 1 acceptance criteria. Export the key and run the
spike before Phase 3 — every structured schema in the design depends on (1), and
the documented fallback ladder is strict → `response_format` JSON mode →
prompt-and-parse with the Q-15 repair loop, resolved **once per run** and
recorded in run metadata.
