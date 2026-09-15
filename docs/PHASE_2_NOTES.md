# Phase 2 notes — patient, gatekeeper, and the encounter state machine

**Status:** complete. **Tests:** 131 passing, zero network access.

---

## 1. What was built

| Path | Purpose |
|---|---|
| `src/agentclinic/graphs/state.py` | `EncounterState`, `Event`, `EncounterSummary`, and `STATE_SOURCES` — the declared writer for every key |
| `src/agentclinic/graphs/routing.py` | `route_action`, `route_stop`, and `check_stop` (a **node**, not a router) |
| `src/agentclinic/graphs/interactive.py` | The human-driven encounter graph with `interrupt()` |
| `src/agentclinic/agents/gatekeeper.py` | Three-tier cascade, leaf-to-parent matching, LLM disambiguation, cost charging |
| `src/agentclinic/agents/patient.py` | `PatientView` → prompt → `PatientReply` |
| `config/test_synonyms.yaml` | 29 test + 11 exam canonical entries, 141 aliases |
| `config/prompts/patient.md`, `gatekeeper_disambiguate.md` | Prompts, editable, out of code |
| `cli.py play` | Drive a real case by hand |

## 2. Run commands

```bash
uv run pytest                                              # 131 tests, offline
uv run python -m agentclinic.cli play medqa-0010 --stub-patient
uv run python -m agentclinic.cli play medqa-0002           # needs OPENROUTER_API_KEY
```

## 3. Verification

| Check | Result |
|---|---|
| Allow-list test: every state key has a declared writer | ✅ 19/19, no orphans |
| No writer is a hidden case field | ✅ |
| Positive leak test: diagnosis present for all 29 flagged cases | ✅ |
| String-scan + checkpoint clean for a leak-free case | ✅ |
| Exact / synonym / case-variant / leaf matching | ✅ |
| Ambiguous leaf returns exactly one panel, never both | ✅ |
| 4 empty-`Test_Results` cases, 16 string-valued, 4 list-valued (2 test + 2 exam) | ✅ |
| Unlisted request charged and logged | ✅ |
| Routing priority, turn delta, post-increment cap, every stop reason | ✅ |
| `interrupt()` suspends before any node runs | ✅ |
| `encounter_log` free of duplication over multiple turns | ✅ |

`CBC` resolves in **76 of 214** cases and misses **zero** where a
`Complete_Blood_Count` key exists.

## 4. Surprises

### 4.1 A smoke test that looked like a failure but was the data

The first gatekeeper check ran against `medqa-0001` and reported `unmatched` for
`CBC`, `complete blood count` and `EKG`. That looked like a broken cascade. It
was not: case 1's `Test_Results` keys are `Blood_Tests`, `Electromyography` and
`Imaging` — it genuinely has no CBC. Re-running against a case that does have
one showed exact, synonym and leaf tiers all working. Worth recording because
the failure mode of *concluding* from that first output would have been to
"fix" a cascade that was already correct.

### 4.2 The LLM tiers degrade to `unmatched` offline, deliberately

Tier 3 and the ambiguous-leaf disambiguation take an injected async callable.
With none supplied — every test, and `--stub-patient` — they return `unmatched`
rather than guessing. That keeps the whole suite offline and deterministic, and
it means the tests measure the deterministic tiers honestly rather than hiding
behind a model. Phase 3 wires the real caller in.

The consequence is visible and intended: `WBC` on case 10 is `unmatched` with
`candidates=('Complete_Blood_Count', 'Urinalysis')` until a disambiguator
exists. Returning both would disclose a urinalysis the doctor never ordered.

### 4.3 LangGraph emits a pending-deprecation warning

`langgraph.checkpoint.base` warns that `allowed_objects` will change default in
a future version. It does not affect behaviour today, and the checkpointer is
used only in interactive mode (Q-30 keeps it out of batch runs), but it is worth
pinning an explicit value when the batch path is built.

### 4.4 `check_stop` had to be a node, and the tests prove why

`route_stop` reads `stop_reason`; `check_stop` writes it. Had the cap logic
stayed in the router — as an earlier plan revision had it — there would have
been no legal writer and every forced stop would have been reported as a
voluntary `finalize`, i.e. a forced-stop rate of exactly zero. The routing
tests assert each stop reason independently.

## 5. Decisions applied, with where to look

- **Q-10** patient answers unknowns → `config/prompts/patient.md`, `unknown` flag
- **Q-11 / D-034** three-tier cascade, no fuzzy tier → `gatekeeper.match`
- **D-035** ambiguous leaf → one panel via the LLM tier → `_parents_with_leaf`
- **Q-12** unlisted still charged → `Gatekeeper.respond`
- **Q-13** unknown price is the table median → `TestCosts.price`
- **Q-31 / D-032** `interrupt()` at the orchestrator → `graphs/interactive.py`
- **C-3** a disabled action raises rather than cycling → `route_action`
- **C-4** `budget_exhausted` short-circuits by edge → `route_action` priority 1
- **C-16** cap evaluated post-increment → `check_stop`

## 6. Pending

Unchanged from Phase 1: the **live spike** still needs `OPENROUTER_API_KEY`.
Until it runs, whether the free model honours strict structured output is
unconfirmed, and Phase 3's orchestrator depends on it.
