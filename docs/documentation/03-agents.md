# 3. Agents

Five agents, plus two advisory sub-roles. Every one goes through `LLMCaller`
(see [LLM layer](05-llm-layer.md)); none holds a model directly.

## Patient — `agents/patient.py`

Answers from `Patient_Actor` and nothing else. Returns `PatientReply(reply,
unknown)`.

Behaviour on facts the case does not cover is `Q-10`'s split, and it is not
arbitrary:

- **A blanket "I don't know" is unrealistic in bulk.** A real patient knows
  whether they smoke, and a doctor quickly learns to read repeated ignorance as
  a signal — which games the encounter.
- **A blanket plausible negative invents findings**, which the brief forbids.

So: **natural negatives for what a patient would know about themselves, explicit
ignorance for anything clinical.** "Do you smoke?" gets an answer; "Do you have
an elevated D-dimer?" gets "I don't know".

The `unknown` flag on every reply makes the rate measurable per run
(`patient_unknown_rate`), which is the only way to notice a patient model that
has started fabricating.

## Gatekeeper — `agents/gatekeeper.py`

Turns a doctor's request into matching case entries. **This is the hardest
non-LLM problem in the codebase.**

The dataset has 234 distinct `Test_Results` keys, 165 appearing once, with
collisions like `Chest_X-ray`/`Chest_X-Ray`, `ECG`/`Electrocardiogram`, and
`Blood_Tests`/`Blood_Work`/`Laboratory_Tests`/`Laboratory_Studies`. Exact lookup
fails constantly — and **every failure becomes a fabricated unavailability that
changes what the doctor believes.**

### The cascade

| Tier | Rule |
|---|---|
| `exact` | normalised equality |
| `contains` | **all** of a key's tokens appear in the request |
| `synonym` | curated alias table, also by token containment |
| `leaf` | a leaf name matches, and exactly one parent holds it |
| `llm_disambiguated` | ambiguous leaf, resolved over **key names only** |
| `llm` | LLM over key names only |
| `unmatched` | nothing — refused, and logged |

### Why `contains` exists — D-045

The first live run resolved **14 of 17 requests through the LLM tier and zero
through exact or synonym.** The alias table — 141 entries at the time — never fired once.

Case keys are terse (`MRI_Brain`, `Complete_Blood_Count`) while a doctor asks in
clinical language ("MRI of the brain with contrast", "CBC with differential").
Requiring equality meant every qualifier defeated the match.

Token containment is **thresholdless and directional** — exact set inclusion, so
`{mri, spine}` is not a subset of `{mri, of, the, brain}` and a request for a
brain MRI can never return a spine MRI. That is what distinguishes it from the
fuzzy tier `D-034` removed, whose threshold had no tuning set and whose two
objectives traded off directly.

The fix also added `electroencephalogram` to the synonym table — **EEG had no
entry at all**, only `electrocardiogram`/ECG, a different test. The table now
holds 46 canonical names and 155 aliases across tests and exams.

> **Live confirmation:** the first web run resolved "MRI brain (with and without
> contrast)" through the `contains` tier to key `MRI_Brain`. Before D-045 that
> request would have fallen through to the LLM.

### Unavailable tests — Q-12 and D-055

A request the case does not contain is refused, logged as an `unlisted_test`
event, and **still charged** `unknown_price`. Free unavailability would let the
doctor probe the key space at no cost, and the cost steward would never feel a
wasted order.

The refusal says the investigation is not part of the case record *at all* and
that rewording will not retrieve it. It does **not** say what the case does
hold: that hands over a hint the real task never gives, and on a single-test case
it is close to naming the answer.

This was found by watching the viewer. On `medqa-0002` the doctor ordered a CSF
JC virus PCR, was told "Not available for this patient.", and **re-asked the same
test reworded on the next turn** — two of eight turns for nothing. The message
read equally as *"you worded that badly"*, so rewording was rational.

The text alone would have fixed nothing, because the orchestrator never saw it.
`summary.tests_ordered` listed request strings with no outcome attached, so a
refused order and a fulfilled one rendered as identical lines. Requests now carry
an `unlisted` marker and the summary renders `[no result: not in this case]`.

## Doctor — `agents/doctor.py`

Three nodes plus two sub-roles.

### `hypothesis`

**The only node that reads `encounter_log`.** Maintains the differential (max 8)
and writes the summary.

`findings` is written by the model **in its own words** (`D-041`). An earlier
version copied event text, which meant the orchestrator read the transcript
*through* the summary — the schema boundary held while the isolation it existed
for did not. Caught by a test, not by review.

Every summary field is capped at 2000 chars, oldest dropped first, and **every
drop is logged as an event** so truncation is visible in the trace rather than
silent.

### `orchestrator`

Chooses exactly one action per turn from a `Literal` type **built per run** from
the enabled action set. A static five-member literal would offer
`search_literature` in configurations where the evidence agent does not exist —
the model would be *correct* to emit it, the output would validate, and the
router would have no edge. A routing failure reachable by the model behaving
properly.

Sees the summary and differential, never the log.

### `finalize`

Produces `FinalAnswer` — diagnosis, differential, confidence, abstain flag, red
flags, rationale. A **forced** finalize (cap or budget) must not call the model:
the budget is already spent, and calling again would breach it.

### `challenger` (panel only)

Argues against the leading diagnosis. Fires every 3rd turn and once before a
voluntary finalize.

**`ChallengerOpinion` has no `should_reopen`** — it is advisory (`D-025`).

Since **D-051** it carries two fields rather than one sentence:

```python
most_dangerous_unexcluded: str
dangerous_alternative_likelihood: Literal["more_likely", "comparable", "less_likely"]
```

The panel's only loss in the live 3-case run was a rank inversion: the correct
diagnosis sat at rank 2 (p=0.30) while the orchestrator ranked another first,
stating *"ranked highest because it is the most dangerous diagnosis if missed"* —
the challenger prompt's own language. The old schema could not express the
distinction its own prompt depended on; `less_likely` was not sayable.

### `cost_steward` (panel only)

Reviews test orders only. **`CostStewardOpinion` has no `replacement_action`** —
also advisory. The order proceeds regardless; the objection informs the *next*
decision.

Both opinions are **one-shot**: the orchestrator clears them after rendering
them into its prompt, which is why it appears as a writer in `STATE_SOURCES`.

> A Phase 4 bug worth knowing: `cost_objection` was in `PanelOutput` but not
> `PanelInput`, and `_CLEARS` wiped it. The objection was produced, carried out,
> cleared, and never read — half of D-025 silently did not happen. The tests were
> green because they covered the half that worked. The defect was inherited from
> the plan, which three adversarial review rounds read without catching.

### `advisory()`

Wraps sub-role nodes so a `StructuredOutputFailed` becomes `None` plus a
`parse_failures` increment rather than killing the case. An advisory node that
cannot produce output degrades to "no opinion".

## Judge — `agents/judge.py`

Scores `FinalAnswer` against `JudgeView`. **The only component that ever sees
ground truth.**

Runs on the **Anthropic SDK, not OpenRouter** — an explicit, recorded override
of the brief's OpenRouter-only rule (`D-021`), for two reasons: judge calls do
not consume the OpenRouter daily allowance, and the judge must never be weaker
than the agents it grades, which matters when the agents run on a free model.

Runs **outside the encounter graph entirely** (`Q-21`), constructed by the eval
runner after the encounter returns and its state is discarded.

```python
class JudgeVerdict(BaseModel):
    match_type: Literal["exact", "synonym", "broader", "narrower", "wrong"]
    entry_matches: list[bool]
    reasoning: str
```

**Deliberately no `correct` field.** Correctness is a *function* of `match_type`
(`Q-22`); a separate boolean would let the model emit
`correct=true, match_type="broader"` and leave two defensible readings of the
headline number. `entry_matches` covers the differential so top-k uses the same
equivalence judgement as the verdict, rather than a string comparison that would
disagree with it.

> **Current status:** verdicts for the 3-case runs were **assigned by hand**
> (`D-047`). They are a one-off reading, not reproducible by re-running, and do
> not satisfy the judge-calibration requirement. Automating this is Phase 5, and
> no accuracy figure produced so far should be compared against an automated run.
