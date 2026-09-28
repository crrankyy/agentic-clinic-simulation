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
has started fabricating. It is one boolean for a whole reply, so on multi-part
questions it is unreliable (M-44, deferred).

### Memory and completeness — D-058

The patient used to receive **only the current question**. It could not keep
its story straight — "I've never been tested for HIV" at turns 9 and 12, "I
don't know if I've ever been tested" at 16 and 17 — while its prompt told it to
repeat earlier answers it could not see. It now receives its own earlier
questions and answers (`patient_history` in `graphs/nodes.py`) — **never test
results or examination findings**, since on medqa-0002 the MRI text names the
diagnosis.

Its prompt also changed on two points the transcripts exposed:

- **Answer the whole topic.** Asked about its "significant medical history",
  it had left out Crohn disease and natalizumab — the facts that make the case —
  because "do not volunteer the rest of your history" was read as "answer
  narrowly". Now: tell everything the case file says about the topic asked.
- **What the case file states is known.** It said three times it was unsure
  whether it was still on a drug its file calls *current treatment*.

In the 2026-09-27 sim both held: asked the same history question, the patient
gave Crohn disease, natalizumab and natural negatives in one consistent answer.

> **Still open:** Q-10's split has no category for a personal fact with no
> natural "negative". Asked whether she still has periods, the medqa-0009
> patient said she could not answer. Any concrete answer would invent a finding.

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

### The synonym tier was not directional — D-057

D-045's directionality held in the contains tier only. On medqa-0002, "MRI
spine", "MRI cervical spine" and "MRI spinal cord" all returned `MRI_Brain` —
whose result names the diagnosis. A modality-only canonical (`mri`) was
accepted for any key *containing* it, discarding the request's region word.

Now a synonym match requires the key's tokens to be covered by the request once
the alias is expanded, the most specific alias wins (as the longest key does in
tier 1b), and the LLM tier's prompt requires the same investigation **and
region**. `normalise` finally drops punctuation, as its docstring always said,
and folds `exam`/`neuro`/`neurologic`, so "neurological exam" matches
`Neurological_Examination` without a model call.

> **Live confirmation:** the first web run resolved "MRI brain (with and without
> contrast)" through the `contains` tier to key `MRI_Brain`. Before D-045 that
> request would have fallen through to the LLM.

### Unavailable, repeated and partial requests — Q-12, D-055, D-056, D-057

A request the case does not contain is refused, logged as an `unlisted_test`
event, and **still charged** `unknown_price` (Q-12): free unavailability would
let the doctor probe the key space at no cost. The refusal says the
investigation is not part of the case record *at all*, and never what the case
does hold.

**D-055 tried to stop re-orders with that wording plus a summary marker, and
failed live.** In `web-single_doctor-1491dcac` the doctor re-ordered refused
tests at turns 7, 10 and 14 *with the marker in its summary at every decision*.
The repeat guard (D-056, in `graphs/ledger.py`) replaced the marker as the
enforcement; the wording stays.

The gatekeeper also now says when an order re-delivers a record: "the same
record already reported at turn N; the case record holds nothing more
specific", **without the payload**. On medqa-0009 — two tests in the case — the
doctor had ordered nine and received the same records again and again. A
bundled request that matched only part ("MRI brain *and spinal cord*") is
marked partial. That check is heuristic and hedged: in the sim it also flagged
a detailed breast-and-axillary exam whose record did cover the axilla.

**A failed model call is no longer a refusal.** The LLM tier used to be wrapped
in `except Exception: return None`; in c79bb4e6 three 429s became "not part of
the case record at all" 2 ms later. Provider failures now propagate to the
harness guard (D-059); only a genuine "no match" answer — the model choosing
`NONE` from a typed list of candidates — is a refusal.

## Doctor — `agents/doctor.py`

Three nodes plus two sub-roles.

### `hypothesis`

**The only node that reads `encounter_log`** — and since D-058, only its
evidence: objective, questions, answers, exams, tests, literature. About a third
of its input had been its own earlier "leading: X" lines, repeated red flags and
bookkeeping, and on the panel the challenger's argument attributed to "doctor".
Its previous differential is passed explicitly, labelled as such. Maintains the
differential (max 8) and writes the summary, including the mechanical ledger
and the progress signal (`leader_since_turn`, `no_yield_streak`, D-060).

`findings` is written by the model **in its own words** (`D-041`). An earlier
version copied event text, which meant the orchestrator read the transcript
*through* the summary — the schema boundary held while the isolation it existed
for did not. Caught by a test, not by review.

Every summary field is capped at 2000 chars, oldest dropped first, and **every
drop is logged as an event** so truncation is visible in the trace rather than
silent.

### `orchestrator`

Chooses exactly one action per turn from a `Literal` type **built per run** from
the enabled action set. A static literal would offer an action in configurations
that disabled it — the model would be *correct* to emit it, the output would
validate, and the router would have no edge. A routing failure reachable by the
model behaving properly.

Sees the summary and differential, never the log. The action list in its
prompt is rendered from the run's enabled set (it used to list a disabled
action).

**The repeat guard (D-056).** Each decision is validated by a per-call subclass
of the decision model. A test or exam that equals, or is a token-subset of, a
request the case refused; one that resolves to a record already delivered; or
a question whose content words overlap an earlier one by ≥ 2/3 — fails
validation. The caller re-asks with the reason, **no turn is spent**, and the
block is logged as a `guard` event. If every proposal in one decision is a
repeat, the encounter ends with `no_new_actions` — scored, and finalize calls
the model normally. Replayed on the recorded transcripts, the guard blocks 12
of 32 actions, all genuine repeats. Its decision — action, reason and expected
information, plus the summary it saw — is written to the trace (M-18).

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

### Advisory failure

The sub-role nodes wear `budget_guarded(..., degrade_keys=(...))`, so a
`StructuredOutputFailed` becomes `None` plus a `parse_failures` increment rather
than killing the case. An advisory node that cannot produce output degrades to
"no opinion".

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
