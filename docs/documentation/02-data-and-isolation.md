# 2. Data and isolation

## The dataset

MedQA OSCE cases from the MIT-licensed
[AgentClinic](https://github.com/SamuelSchmidgall/AgentClinic) repository,
pinned to commit `b6570ede…`. **Only the data is used — no code is copied or
imported from that project.**

| File | Cases | Used for |
|---|---|---|
| `agentclinic_medqa_extended.jsonl` | 214 | Everything |

The 107-case `agentclinic_medqa.jsonl` was downloaded until D-064 and never
loaded; the script now fetches only the file the pipeline reads.

The `.jsonl` files are **not committed**. They are reproducible from the pinned
SHA plus the SHA-256 in `dataset/MANIFEST.json`, which *is* committed.

```bash
uv run python server/scripts/download_dataset.py
```

The script writes atomically (`.part` then `os.replace`), verifies SHA-256 and
non-blank line count, and **halts without overwriting** on a mismatch, printing
expected versus actual. A second run is a no-op.

## One case

```
OSCE_Examination
├── Objective_for_Doctor            → the referral line; the doctor's only seed
├── Patient_Actor                   → Demographics, History, Symptoms, …
├── Physical_Examination_Findings   → keyed by region/exam
├── Test_Results                    → keyed by test name
├── Correct_Diagnosis               → ground truth
└── Management_and_Follow_Up        → ground truth
```

### Why the nested shapes stay generic

`Test_Results` has **234 distinct top-level keys across 214 cases, 165 of them
appearing exactly once.** There is nothing stable to model, so values are typed
`str | dict | list` rather than forced into a schema.

That irregularity is real, not theoretical:

- 16 cases have **string** values at the top level of `Test_Results`
- 2 contain **lists** nested inside (cases 153 and 185)
- `Physical_Examination_Findings` contains lists in 2 more (cases 37 and 103)
- 4 cases have **empty** test results
- `Patient_Actor.Review_of_Systems` is a dict in 10 cases
- `Past_Medical_History` is a list or dict in 3

**Undocumented `Patient_Actor` keys are preserved, not dropped** (`D-018`):
`Current_Medications`, `Medications`, `Drug_History`, `Family_History`. A
patient who is on medications must not answer *"I am not on any medications"* —
that is a fabricated negative, which the brief forbids.

### Loading

A malformed record **fails the whole load**, naming the line (`Q-02`). The file
has zero malformed lines; resilience here would only buy the ability to evaluate
silently on a partial dataset.

Case IDs are `medqa-0001` … `medqa-0214` — the 1-based line number, zero-padded
(`Q-01`). Stable because the download is pinned, and readable in trace filenames,
which matters more than content addressing for a project whose main activity is
reading traces.

## The four views

`CaseStore` holds `Case` objects outside graph state and **exposes no method
that returns one**. A test asserts this by reflection.

| View | Carries | Used by |
|---|---|---|
| `DoctorView` | `objective_for_doctor` | doctor nodes |
| `PatientView` | `Patient_Actor` | patient agent |
| `GatekeeperView` | exams + tests | gatekeeper |
| `JudgeView` | **`Correct_Diagnosis`**, `Management_and_Follow_Up` | judge only |
| `CaseMetadata` | ids, line number, leakage flags | eval runner |

Each is a **frozen dataclass** carrying a disjoint slice. Views are bound into
nodes by closure at construction time, never through `config["configurable"]` —
LangGraph records config in checkpoint metadata.

`CaseMetadata` exists so the runner can get bookkeeping (leakage flags, line
numbers) without touching a view that has ground truth on it.

## Leakage: measured, not hidden

Some cases embed the diagnosis in the data the gatekeeper is *designed* to
return. Isolation cannot fix that — it is the benchmark's property — so it is
flagged per case and reported.

```python
def dx_in_results(diagnosis, test_results, exam_findings) -> bool:
    needle = normalise(strip_trailing_parenthetical(diagnosis))
    return bool(needle) and needle in _results_blob(test_results, exam_findings)
```

| Flag | Fires on | Meaning |
|---|---|---|
| `dx_in_results` | **29 of 214** | The exact diagnosis string appears in results |
| `dx_tokens_in_results` | **4** — cases 13, 34, 123, 208 | All diagnosis tokens appear, scattered |

Two details are load-bearing and were each found by a failing test:

- **Keys must be matched, not only values.** Case 154's key is
  `Varicella_Specific_Tests` — the diagnosis is in the key name.
- **A trailing parenthetical must be stripped.** Case 104's diagnosis is
  `Legg-Calvé-Perthes disease (LCPD)`, which never appears verbatim.

> **Where this bites:** one of the three evaluation cases (`medqa-0002`) is a
> `dx_in_results` case by construction, so the leak-free breakdown in any report
> on those three is over **two** cases. The report says so rather than printing a
> figure that looks like it covers three.

### The positive test

There is a test asserting the diagnosis **is** present in gatekeeper output for
the flagged cases. It sounds backwards, and it is the reason a real bug was
caught: `json.dumps` defaults to `ensure_ascii=True`, which escaped non-ASCII
characters and made the isolation test pass falsely for four diagnoses. A
one-directional test would still be green today.

## Splits

**Grouped, not stratified** (`Q-36`, `D-024`). The distinction matters: to
*stratify* by a label puts every label in **both** splits in proportion — the
exact opposite of what is wanted. Grouping keeps each `Correct_Diagnosis` wholly
on one side, so a prompt tuned on a dev case cannot leak signal into held-out
through a duplicate.

An early draft of the plan said "stratified". Since the split is committed once
and never regenerated, that would have created the leak permanently.

Grouping keys are **case-insensitive**: 31 diagnosis strings repeat across 63
cases as written, but 35 across 72 once normalised. Five diagnoses differ only in
capitalisation (`Cardiac Contusion` / `Cardiac contusion`, and four more).

| Split | Cases |
|---|---|
| `dev` | 40 |
| `heldout` | 174 |

`dataset/splits.json` **is** committed, so splits can never silently differ
between machines or re-runs. The fixed three-case evaluation subset is
`medqa-0002`, `medqa-0009`, `medqa-0012`, drawn from dev by
`select_eval_subset`.
