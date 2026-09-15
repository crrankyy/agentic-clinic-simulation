# Phase 0 notes — dataset

**Status:** complete. Download tooling built and verified, inspection done,
and all four discrepancies with Section 3 resolved by user decision
(D-015 – D-018).

---

## 1. What was built

| Path | Purpose |
|---|---|
| `scripts/download_dataset.py` | Idempotent download, SHA-256 + case-count verification, atomic writes, `MANIFEST.json` |
| `tests/test_download_dataset.py` | 14 offline tests via `httpx.MockTransport`; no network |
| `dataset/MANIFEST.json` | Provenance: source URL, commit SHA, download time, SHA-256, case count |
| `pyproject.toml`, `.python-version`, `uv.lock` | Python 3.12.13, `httpx` 0.28.1, `pytest` 8.4.2 |
| `.gitignore` | `.jsonl` ignored, `MANIFEST.json` tracked |
| `docs/BRIEF.md`, `docs/DECISIONS.md` | Brief verbatim (45 `[ASK]` markers); D-001 … D-014 |

## 2. Run commands

```bash
uv sync                                       # create the 3.12 env
uv run python scripts/download_dataset.py     # download + verify + manifest
uv run pytest                                 # 14 tests, offline
```

## 3. Verification performed

| Check | Result |
|---|---|
| Interpreter | Python 3.12.13 (system 3.14.7 untouched) |
| Case counts vs brief | **214** and **107** — exact match |
| Blank lines | 0 in both files |
| Malformed JSON lines | 0 in both files |
| Trailing newline | Absent on the last line of both files (the brief warned of this) |
| Idempotent re-run | Reports "up to date", issues no HTTP request |
| Tamper test (D-004) | Halts, exit 2, prints both hashes, file left unmodified |
| Malformed manifest | Halts rather than overwriting |
| `git add dataset/` | Stages only `MANIFEST.json` (D-006 satisfied) |
| Test suite | 14 passed in 0.27s |

## 4. Structure — confirmed against Section 3

Every one of the 321 records has exactly one top-level key, `OSCE_Examination`.
All five documented sub-keys are present in 214/214 and 107/107 with the
documented types: `Objective_for_Doctor` (str), `Patient_Actor` (dict),
`Physical_Examination_Findings` (dict), `Test_Results` (dict),
`Correct_Diagnosis` (str).

### Key inventory — `Test_Results` (the gatekeeper's problem)

Extended file: **234 distinct top-level keys**, mean 2.5 per case,
**165 of them appear exactly once**. Nesting depth 0–3.

| Key | n | | Key | n |
|---|---:|---|---|---:|
| `Complete_Blood_Count` | 70 | | `Biopsy` | 9 |
| `Imaging` | 38 | | `Liver_Function_Tests` | 8 |
| `Blood_Tests` | 16 | | `Blood_Work` | 7 |
| `Urinalysis` | 15 | | `Laboratory_Studies` | 7 |
| `Skin_Biopsy` | 13 | | `ECG` | 6 |
| `Chest_X-ray` | 13 | | `Chest_X-Ray` | 5 |
| `Echocardiogram` | 11 | | `Peripheral_Blood_Smear` | 5 |
| `Laboratory_Tests` | 10 | | `Chemistry_Panel` | 5 |

The long tail is the real design constraint. Note the collisions already
visible in the top 25 alone: `Chest_X-ray` vs `Chest_X-Ray` (case only),
`ECG` vs `Electrocardiogram`, `Electrolytes` vs `Serum_Electrolytes`,
`Blood_Tests` vs `Blood_Test` vs `Blood_Work` vs `Laboratory_Tests` vs
`Laboratory_Studies`. Exact-match lookup would fail constantly; this is
direct evidence for the Section 5.2 matching-strategy question.

### Key inventory — `Physical_Examination_Findings`

Extended file: **78 distinct top-level keys**, mean 2.5 per case, 41 singletons.
`Vital_Signs` appears in 212/214. Same synonym problem:
`Neurological_Examination` (35) vs `Neurologic_Examination` (5),
`Cardiovascular_Examination` (12) vs `Cardiac_Examination` (5),
`Skin_Examination` (28) vs `Dermatological_Examination` (7) vs `Skin` (3).

### `Correct_Diagnosis`

4–74 characters, median 21. 182 distinct values across 214 cases; 31 strings
repeat, covering 63 cases. No byte-identical duplicate records. Capitalisation
is inconsistent (163 sentence case, 51 title case) and 7 carry a parenthetical
abbreviation, e.g. `'Progressive multifocal encephalopathy (PML)'`. This
matters for the Section 5.5 judge: exact string comparison is unusable.

---

## 5. Discrepancies with Section 3 — reported and resolved

### D1. Inherent diagnosis leakage through the gatekeeper — **most serious**

> **Resolved by D-015:** flag each case with `dx_in_results`; every report gives
> accuracy over all 214 *and* over the 187 leak-free cases. Not fixable by state
> design, so it is measured rather than hidden.

In **27 of 214 cases (12.6%)** the exact `Correct_Diagnosis` string appears
verbatim inside `Physical_Examination_Findings` / `Test_Results` — the fields
the gatekeeper is designed to hand to the doctors. Examples:

- line 3, dx `Hirschsprung disease` → `barium_enema.findings`:
  *"a transition zone in the distal colon, compatible with **hirschsprung disease**"*
- line 23, dx `Malignant melanoma` → `skin_biopsy.histopathology_findings`:
  *"atypical melanocytes ... confirming the diagnosis of **malignant melanoma**"*
- line 2, dx `Progressive multifocal encephalopathy (PML)` → `mri_brain.findings`:
  *"lesions consistent with **progressive multifocal encephalopathy (pml)**"*

Loosening to "any content word of the diagnosis" raises it to 77/214 (36%) for
gatekeeper fields, 22/214 for `Patient_Actor`, 11/214 for `Objective_for_Doctor`.
The exact string never appears in `Patient_Actor` or `Objective_for_Doctor`.

This is **not** fixable by state design. The brief's isolation requirement stops
`Correct_Diagnosis` reaching doctors *through the state channel*; here the
ground truth is embedded in data the doctors are supposed to receive. Ordering
one test can hand over the answer, which directly affects what the accuracy
numbers mean.

### D2. Undocumented field `Management_and_Follow_Up`

> **Resolved by D-016:** hidden, same visibility class as `Correct_Diagnosis`.

Line 133 (`Polyostotic Fibrous Dysplasia`) carries a sixth `OSCE_Examination`
key the brief does not list, containing referral and treatment plans. Treatment
strongly implies diagnosis, so its visibility class must be assigned explicitly.

### D3. `agentclinic_medqa.jsonl` is a strict prefix of the extended file

> **Resolved by D-017:** kept for provenance, never evaluated on. The extended
> file is the single source of truth; the dev subset comes from a seeded split.

All 107 records are byte-identical to `extended[0:107]`, in the same order.
It is not an independent set. Consequences: the two files must never be
combined (double counting), and D-002's stated rationale — a second file to
validate the loader against — does not hold, though it remains usable as a
fast dev subset.

### D4. Structural irregularities the brief does not mention

> **Resolved by D-018:** varying fields modelled as optional, everything
> preserved, all 214 cases remain usable.

| Issue | Count | Lines |
|---|---:|---|
| `Test_Results` is `{}` (no tests at all) | 4 | 69, 106, 111, 209 |
| `Test_Results` maps key → plain string, not a nested dict | 2 | 150, 187 |
| `Patient_Actor` missing `Past_Medical_History` | 1 | 120 |
| `Patient_Actor.Symptoms` is `{}` (no `Primary_Symptom`) | 1 | 132 |
| Undocumented `Patient_Actor` keys: `Current_Medications` (5), `Medications` (2), `Drug_History` (1), `Family_History` (1) | 9 | 18, 21, 26, 43, 77, 98, 132, 179, 191 |

None are malformed JSON, so the Section 3 "malformed records" question does not
cover them. They are schema irregularities the loader and gatekeeper must
handle deliberately.

---

## 6. Surprises worth remembering

- The `Test_Results` key space is far more fragmented than Section 3's "common
  categories" list suggests: 165 of 234 keys occur exactly once.
- Four cases have no test results at all, so "order a test" must have a defined
  behaviour even when the case offers nothing.
- The 107-case file buys no additional coverage whatsoever.
- uv publishes no x86_64 macOS bottle, so `brew install uv` tried to compile
  LLVM from source (see D-014).
