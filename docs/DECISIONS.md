# Decision log

Every decision the user has made on this project. Per Rule 0.1 of the brief:
check this file before asking a question; do not re-ask what is already
decided; if a later situation appears to conflict with a recorded decision,
ask the user rather than reinterpreting it.

Each entry records: **date | question | options offered | decision | reason**.

Open questions awaiting a decision live in `docs/DECISIONS_OPEN.md`.

---

## D-001 — Dataset download mechanism

- **Date:** 2026-09-15
- **Question:** How should the dataset files be fetched?
- **Options:** (a) Python download script; (b) one-off `curl` commands now;
  (c) shell script using `curl` + `shasum`.
- **Decision:** (a) `scripts/download_dataset.py` — idempotent, verifying, testable.
- **Reason:** Section 11 requires download tests (checksum mismatch, line-count
  mismatch, mocked network failure). Those need a testable target. A one-off
  command is not reproducible, and checksum/manifest logic in shell is clumsy
  and hard to unit-test with a mocked transport.

## D-002 — Which dataset files to download

- **Date:** 2026-09-15
- **Question:** Which of the four upstream files should be downloaded?
- **Options:** (a) both MedQA files; (b) all four; (c) `medqa_extended` only.
- **Decision:** (a) `agentclinic_medqa_extended.jsonl` (214 cases, primary) and
  `agentclinic_medqa.jsonl` (107 cases). No NEJM files.
- **Reason:** The smaller file gives a fast dev loop and a second file to
  validate the loader against. NEJM has a different schema and is Phase 7 only,
  which the brief says happens only on explicit request; downloading it now
  would invite premature work on it.

## D-003 — Pin to an upstream commit

- **Date:** 2026-09-15
- **Question:** Should downloads pin to a commit SHA or track `main`?
- **Options:** (a) pin to `b6570ede...`; (b) track `main`.
- **Decision:** (a) Pin to `b6570edefb940857a7c334350656b29f9d984f24`, which was
  upstream `HEAD` on 2026-09-15.
- **Reason:** An unpinned `main` can change the data between runs, which
  invalidates saved splits, recorded checksums, and any evaluation baseline
  already measured. Updating the pin later is a deliberate one-line change.

## D-004 — Behaviour on SHA-256 mismatch for an existing file

- **Date:** 2026-09-15
- **Question:** If a dataset file exists but its hash differs from `MANIFEST.json`, what happens?
- **Options:** (a) stop and report, no overwrite; (b) re-download and overwrite;
  (c) re-download alongside as `<file>.new`.
- **Decision:** (a) Do not overwrite, do not delete. Print expected vs actual
  hash and the path, exit non-zero, ask the user.
- **Reason:** A mismatch means one of three different things — upstream changed,
  the file was edited locally, or a download was corrupted — and each needs a
  different human response. Overwriting silently destroys the evidence.

## D-005 — Version control

- **Date:** 2026-09-15
- **Question:** Should the project root be a git repository?
- **Options:** (a) `git init` now; (b) no git.
- **Decision:** (a) Initialise git, commit at each phase boundary. Never commit
  or push without asking the user first.
- **Reason:** Section 10's Phase 3/4/5 adversarial reviews are scoped to "the
  diff", which requires history. It also makes an unwanted phase revertible.

## D-006 — What under `dataset/` is tracked

- **Date:** 2026-09-15
- **Question:** Should `dataset/` be git-ignored?
- **Options:** (a) ignore `.jsonl`, commit `MANIFEST.json` + `splits.json`;
  (b) commit all of `dataset/`; (c) ignore all of `dataset/`.
- **Decision:** (a).
- **Reason:** No third-party data is vendored, yet reproducibility is preserved
  because the pinned SHA (D-003) plus the recorded SHA-256 fully determine the
  files. `splits.json` stays tracked so evaluation splits can never silently
  differ between machines — option (c) was rejected specifically for that risk.

## D-007 — Python version and package manager

- **Date:** 2026-09-15
- **Question:** Which Python version and package manager?
- **Options:** (a) `uv` + Python 3.12; (b) `uv` + 3.13; (c) `pip`/venv on the
  system 3.14; (d) `poetry` + 3.12.
- **Decision:** (a) `uv` with a pinned Python **3.12**.
- **Reason:** The system Python is 3.14.7, newer than much of the
  LangChain/LangGraph dependency tree currently ships wheels for. `uv` manages
  its own interpreter, so the system Python is untouched, and `uv.lock` gives
  exact reproducibility. 3.12 has the broadest wheel coverage of the candidates.

## D-008 — Directory layout

- **Date:** 2026-09-15
- **Question:** Approve the Section 12 layout?
- **Options:** (a) as written; (b) flatten, dropping `src/`; (c) as written but
  create the full tree upfront with `.gitkeep`.
- **Decision:** (a) As written, with `/Users/muditgaur/misc/agent-clinic` as the
  repo root. Directories are created only when a phase needs them.
- **Reason:** The `src/` layout stops tests from accidentally importing the
  working-directory copy instead of the installed package, which would hide
  packaging bugs. Creating the tree lazily avoids a forest of empty directories
  and avoids front-loading structure before the design has been reviewed.

## D-009 — How the remaining decisions are collected

- **Date:** 2026-09-15
- **Question:** How should the ~45 remaining `[ASK]` items be collected (Step 2)?
- **Options:** (a) a written questionnaire document answered in one pass;
  (b) ~12 interactive rounds of four questions; (c) questionnaire for mechanical
  items plus interactive rounds for the consequential ones.
- **Decision:** (a) `docs/DECISIONS_OPEN.md` — every question grouped by brief
  section, each with why-it-matters, 2–4 options with trade-offs, and a clearly
  labelled recommendation. Answers are then folded into this file.
- **Reason:** Fewest interruptions, and the user can see all questions at once
  to spot interactions between coupled decisions (e.g. max turns vs recursion
  limit vs spend cap).

## D-010 — Location of the Phase 0 inspection report

- **Date:** 2026-09-15
- **Question:** Where does the dataset inspection report go?
- **Options:** (a) `docs/PHASE_0_NOTES.md`; (b) a separate
  `docs/DATASET_INSPECTION.md`; (c) terminal output only.
- **Decision:** (a).
- **Reason:** Section 10 already requires `docs/PHASE_N_NOTES.md` per phase.
  One document per phase, nothing extra to track. Terminal-only was rejected
  because later phases genuinely need the `Test_Results` key inventory.

## D-011 — Installing `uv`

- **Date:** 2026-09-15
- **Question:** How should `uv` be installed?
- **Options:** (a) the user runs the installer via `!`; (b) Claude runs it via
  Bash; (c) the user installs it independently beforehand.
- **Decision:** (a) The user runs `brew install uv` themselves.
- **Superseded by:** D-014 (no x86_64 macOS bottle exists; see below).
- **Reason:** A network installer that modifies the user's machine should run
  under their hand, not the agent's. Homebrew is present, so the `curl | sh`
  form is unnecessary.

## D-012 — Existing dataset file with no `MANIFEST.json` entry

- **Date:** 2026-09-15
- **Question:** A dataset file is on disk but the manifest has no entry for it
  (manifest deleted, or the file was placed there by hand). What should the
  download script do?
- **Options:** (a) download to `.part`, compare hashes, adopt if identical and
  stop if different; (b) re-download and overwrite unconditionally; (c) stop
  immediately without any network call.
- **Decision:** (a). If the freshly downloaded bytes hash-match the existing
  file, keep the file and simply write the manifest entry. If they differ,
  report both hashes and stop without overwriting.
- **Reason:** Section 3 says "otherwise re-download", but D-004 exists precisely
  so that a file of uncertain provenance is never silently destroyed. This
  satisfies the letter of Section 3 while keeping D-004's safety property, and
  it makes the benign case (manifest deleted to force a refresh) a no-op instead
  of an error.

## D-013 — Default git branch name

- **Date:** 2026-09-15
- **Question:** `git init` produced `master`. Keep or rename?
- **Options:** (a) rename to `main`; (b) keep `master`.
- **Decision:** (a) `main`.
- **Reason:** Current convention and matches the default if the repository ever
  gains a remote. Free to do before any history exists.

## D-014 — `uv` installation method (supersedes D-011)

- **Date:** 2026-09-15
- **Question:** `brew install uv` began compiling `llvm@22` from source because
  uv publishes **no x86_64 macOS bottle** (tags are `arm64_golden_gate`,
  `arm64_linux`, `arm64_sequoia`, `arm64_sonoma`, `arm64_tahoe`,
  `x86_64_linux`) and this machine is Intel. How should we proceed?
- **Options:** (a) stop the brew build and use Astral's standalone installer;
  (b) let the `llvm@22` → `rust` → `uv` source build finish (est. 1–3 h);
  (c) abandon `uv` for `python3.12 -m venv` + pip.
- **Decision:** (a). The user runs
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, which fetches the prebuilt
  `uv-x86_64-apple-darwin` binary into `~/.local/bin`. Claude stopped the
  running brew build.
- **Reason:** A multi-hour LLVM compile blocks all of Phase 0 and could still
  fail late on the Command Line Tools mismatch Homebrew warned about. The
  standalone binary preserves D-007 exactly, and uv will reuse the Python
  3.12.13 already present at `/usr/local/opt/python@3.12` instead of
  downloading an interpreter. Option (c) was rejected because it loses
  `uv.lock`, and D-007 chose uv specifically for lockfile reproducibility.
- **Supersedes:** D-011 (the *method* changed; the principle that the user runs
  the installer themselves is unchanged).

## D-015 — Diagnosis text embedded in gatekeeper-visible case fields

- **Date:** 2026-09-15
- **Question:** In 27/214 cases the exact `Correct_Diagnosis` string appears
  verbatim inside `Physical_Examination_Findings` / `Test_Results` — the fields
  the gatekeeper hands to doctors (e.g. line 23, `Malignant melanoma`, skin
  biopsy reads "confirming the diagnosis of malignant melanoma"). How should
  this be handled?
- **Options:** (a) flag per case and report accuracy over all cases *and* over
  the leak-free subset; (b) redact the diagnosis string in gatekeeper output;
  (c) exclude the 27 cases; (d) ignore it.
- **Decision:** (a). Compute a per-case `dx_in_results` flag at load time.
  Every report gives accuracy over all 214 and over the 187 leak-free cases.
- **Reason:** State-design isolation cannot fix this — the ground truth is
  inside data the doctors are *supposed* to receive. Flagging keeps fidelity to
  the original benchmark while making the effect measurable rather than
  silently inflating the headline number. Redaction would break comparability
  and leave a conspicuous artefact a model could exploit; exclusion would bias
  the set against cases whose decisive test is a naming biopsy or scan.
- **Implication:** `dx_in_results` becomes a per-case metric (Section 8) and the
  report gains a leak-free breakdown.

## D-016 — Visibility of `Management_and_Follow_Up`

- **Date:** 2026-09-15
- **Question:** Line 133 carries an undocumented sixth `OSCE_Examination` key
  containing referral and treatment plans. What visibility class?
- **Options:** (a) hidden, same class as `Correct_Diagnosis`; (b)
  gatekeeper-visible on request; (c) dropped at load time.
- **Decision:** (a) Hidden. It is never written into any state channel a
  doctor-side node can read, and never enters encounter-graph checkpoints.
- **Reason:** A treatment plan implies the diagnosis, so exposing it is close to
  exposing the answer. It occurs in exactly one case, so there is no cost to
  hiding it. Dropping it was rejected because a loader that silently discards
  fields is the kind of quiet behaviour that erodes trust in the pipeline.

## D-017 — Role of `agentclinic_medqa.jsonl`

- **Date:** 2026-09-15
- **Question:** The 107-case file is byte-identical to the first 107 records of
  the extended file, in order. What role should it have?
- **Options:** (a) keep downloaded for provenance, never evaluate on it;
  (b) stop downloading it; (c) use it directly as the dev subset.
- **Decision:** (a). `agentclinic_medqa_extended.jsonl` is the single source of
  truth for all evaluation. The dev subset comes from a seeded split over the
  214. The two files are never combined.
- **Reason:** A prefix is not a random sample, so using it as a dev split risks
  systematically unrepresentative development results. D-002 stands, but its
  stated rationale (a second file to validate the loader against) was wrong and
  is hereby corrected.

## D-018 — Handling structural irregularities in the case records

- **Date:** 2026-09-15
- **Question:** How should the loader treat the irregularities found in
  inspection — 4 cases with `Test_Results == {}`, 2 with `Test_Results` mapping
  keys to plain strings rather than nested dicts, 1 missing
  `Past_Medical_History`, 1 with `Symptoms == {}`, and 9 carrying undocumented
  `Patient_Actor` keys (`Current_Medications`, `Medications`, `Drug_History`,
  `Family_History`)?
- **Options:** (a) model the varying fields as optional and preserve everything;
  (b) strict schema, reject irregular records; (c) preserve documented fields
  but drop unknown keys.
- **Decision:** (a). Varying fields are optional in the pydantic models;
  undocumented `Patient_Actor` keys are preserved and visible to the patient
  agent; `Test_Results == {}` routes through the gatekeeper's
  not-available path (value still to be decided, Section 5.2). All 214 cases
  remain usable.
- **Reason:** These are schema irregularities, not malformed records, so the
  Section 3 malformed-record question does not cover them. Rejecting them would
  discard ~9–15 real cases from a small set for tidiness rather than a data
  quality reason. Dropping unknown patient-side keys was rejected because a
  patient who is on medications would then answer "I am not on any
  medications" — a fabricated negative, which Section 5.1 forbids.
