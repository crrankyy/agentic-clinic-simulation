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
- **⚠️ Corrected 2026-09-15** (PLAN_REVIEW #8): the count is rule-dependent and
  the field was wrong. Verified: **29/214** under the adopted rule (normalise
  case, strip a trailing parenthetical, match flattened keys **and** values);
  27 under values-only matching. The string appears in **zero**
  `Physical_Examination_Findings` cases. The leak-free denominator is **185**,
  not 187, and is computed at load time, never hard-coded.
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
  inspection — 4 cases with `Test_Results == {}`, **16** with string values at
  the top level of `Test_Results` rather than nested dicts (*corrected
  2026-09-15 from "2": 2 cases are all-string, 14 are mixed str/dict; a further
  2 cases, 153 and 185, contain lists — so loader and gatekeeper must handle
  `str | dict | list` at every level*), 1 missing
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

---

# Step 2 decisions (2026-09-15)

## D-019 — All 41 questionnaire recommendations accepted

- **Date:** 2026-09-15
- **Question:** The 41 open `[ASK]` items collected in `docs/DECISIONS_OPEN.md`.
- **Options:** Each question's full option analysis and trade-offs are recorded
  in `docs/DECISIONS_OPEN.md` under the matching `Q-nn` heading; they are not
  duplicated here.
- **Decision:** All 41 recommendations accepted as written, **except** where
  amended by D-020 … D-024 below.
- **Reason:** User reviewed the questionnaire and accepted the recommended
  option for each item.

| Q | Decision |
|---|---|
| Q-01 | Case IDs `medqa-0001`…`medqa-0214`, 1-based line number in the extended file, zero-padded |
| Q-02 | Malformed records fail loudly; naming the offending line numbers. No partial loads |
| Q-03 | `ChatOpenAI` against the OpenRouter base URL, plus a callback handler capturing OpenRouter's raw `usage` block |
| Q-04 | *Amended — see D-020 and D-021* |
| Q-05 | Providers pinned (`allow_fallbacks: false`) for reported runs; resolved provider recorded on every call |
| Q-06 | `X-Title: agent-clinic`; no `HTTP-Referer` |
| Q-07 | Inline `usage` with `usage: {include: true}`; local price table used only as a pre-flight estimator |
| Q-08 | 3 retries, exponential backoff 1/2/4s with jitter, 120s timeout, retry only 429/500/502/503/504 and connect/read timeouts. **API failures are recorded as `error` and excluded from accuracy, never scored as wrong** |
| Q-09 | Case store outside graph state; graph state carries only `case_id`; per-role restricted views injected via config/closures |
| Q-10 | Patient gives plausible negatives for self-knowable facts, explicit ignorance for clinical facts; `unknown` marker required in structured output |
| Q-11 | Gatekeeper cascade: exact (normalised) → curated synonyms → `rapidfuzz` above threshold → LLM fallback over **key names only**. Matching tier logged |
| Q-12 | Unlisted test returns "Not available for this patient" **and still charges the test cost**; logged as an unlisted-test event |
| Q-13 | Hand-written illustrative `config/test_costs.yaml` (~30 categories, header states prices are illustrative); unknown tests priced at the table **median** |
| Q-14 | Differential capped at 8 entries |
| Q-15 | 2 repair retries on invalid structured output, then forced `finalize` with `abstain=true` and a distinct `parse_failure` outcome column |
| Q-16 | Challenger runs before every `finalize` and every 3rd turn; cost-steward before every `order_test` |
| Q-17 | Brief's schema, with `red_flag` as a **list of named concerns** rather than a boolean. Differential probabilities are not forced to sum to 1; normalised at analysis time |
| Q-18 | `tool=agent-clinic`; `email` from `NCBI_EMAIL`; evidence agent is skipped entirely if unset |
| Q-19 | Evidence HTTP cache at `runs/.cache/evidence/`, keyed by normalised query, 30-day expiry; hit/miss counts in the run report |
| Q-20 | Uncited or invalid claims are stripped and logged; per-run `uncited_claims` count. PMID validation is mechanical, in code |
| Q-21 | Judge runs as a **separate graph**, invoked by the eval runner after the encounter graph returns |
| Q-22 | `exact` and `synonym` count as correct (headline); `broader`/`narrower` recorded and reported as a secondary lenient accuracy |
| Q-23 | top-k at k = 1, 3, 5, plus full-list containment |
| Q-24 | Max 20 turns per encounter for reported runs; CLI-overridable to 10 for development. Forced-stop rate reported |
| Q-25 | Spend caps $0.50 per case, $25 per run; checked before each call against the running total; pre-flight estimate refuses over-budget runs; partial results always written |
| Q-26 | **No confidence-threshold stop rule.** Encounters end on `finalize`, turn cap, or spend cap |
| Q-27 | Recursion limit = `max_turns × 6 + 20`, derived in code from the turn cap so the two cannot drift |
| Q-28 | Red flags both model-reported and rule-based on vital signs; **log only**, never stops the encounter |
| Q-29 | Structured running summary (`findings`, `tests_ordered`, `ruled_out`, `open_questions`), each field capped ~2000 chars, oldest dropped first, drops recorded. Raw messages stay in traces, never resent |
| Q-30 | `MemorySaver` for the Phase 2 interactive mode only; **no checkpointer for batch eval runs**. Case-level resume belongs to the eval runner via `results.csv` |
| Q-31 | Human-in-the-loop via LangGraph `interrupt()` at the orchestrator, resumed with `Command(resume=...)` |
| Q-32 | **No LangSmith.** Local JSONL traces and a `rich` viewer only; nothing leaves the machine |
| Q-33 | *Amended — see D-023* |
| Q-34 | Response cache built, keyed by (model, messages, params); `--cache` for development; **hard-disabled by a code-level assertion** for any run that writes a report; cache state recorded in run metadata |
| Q-35 | `langgraph`, `langchain-core`, `langchain-openai`, `pydantic`, `httpx`, `pyyaml`, `rich`, `typer`, `rapidfuzz`, `pytest`, `pytest-asyncio`, `respx`. **Plus `anthropic`** per D-021. Exact versions frozen in `uv.lock` |
| Q-36 | Splits dev 40 / heldout 174, seed 20260915, stratified so no `Correct_Diagnosis` string spans both splits |
| Q-37 | `single_doctor` is a **separate, simpler graph**, confirmed |
| Q-38 | Wilson score 95% intervals for accuracy; paired bootstrap for panel-minus-single differences; 10 calibration bins with per-bin counts shown |
| Q-39 | ~12 hand-written leakage probes in `config/prompts/leakage_probes.yaml`, versioned; user approves the set before Phase 6 |
| Q-40 | Counterfactual cases: automated keyword shortlist, then **user approves the final list** before any run |
| Q-41 | *Amended — see D-022* |

## D-020 — Free OpenRouter models for all agent roles (amends Q-04)

- **Date:** 2026-09-15
- **Question:** Which model for the patient, gatekeeper, doctor sub-roles and
  evidence agent?
- **Options:** (a) tiered paid models as recommended in Q-04; (b) free models
  throughout; (c) cheap paid models throughout (~$14–20 for the full matrix).
- **Decision:** (b). **`nvidia/nemotron-3-super-120b-a12b:free`** for every
  OpenRouter role. Paid options to be revisited later.
- **Reason:** User preference to avoid spend for now. This model is the largest
  free model on OpenRouter supporting both tool calling and **strict**
  structured outputs (262k context), which Q-11, Q-15 and Q-17 all depend on.
  Using one model for every role also removes any role/model confound.
- **Known constraint:** free models are capped at 20 requests/minute and, with
  ≥$10 of credits purchased (confirmed, see D-023), 1000 requests/day. This is
  why the evaluation scope is reduced (D-022).

## D-021 — Judge via the Anthropic SDK (overrides brief §0.2)

- **Date:** 2026-09-15
- **Question:** How should the judge agent be run?
- **Options:** (a) Anthropic SDK with `claude-opus-5`; (b) Anthropic SDK with
  `claude-sonnet-5`; (c) keep the judge on OpenRouter per brief §0.2.
- **Decision:** (a). Python `anthropic` SDK, model **`claude-opus-5`**,
  structured output via `output_config` / `client.messages.parse()`.
  Authenticated with the user's **Claude subscription** through
  `ant auth login`, which stores a profile the SDK resolves automatically — no
  `ANTHROPIC_API_KEY` is set and none is needed.
- **Reason:** User decision. Two real benefits: judge calls no longer consume
  the OpenRouter free-tier daily budget, and it satisfies the Q-04 caveat that
  the judge must never be weaker than the agents being judged — which matters a
  great deal now that the agents run on a free model (D-020).
- **⚠️ Explicit override:** brief §0.2 states *"LLMs are called through
  OpenRouter, not the Anthropic SDK or any provider-specific SDK."* The user
  has knowingly overridden this for the judge only. Every other role stays on
  OpenRouter. This is recorded here so the deviation is never mistaken for an
  oversight.
- **Implications:** `anthropic` joins the dependency list (Q-35); the judge has
  its own cost-accounting path separate from OpenRouter's; `ant` CLI must be
  installed and `ant auth login` run before any judged run.

## D-022 — Evaluation scope reduced to 3 cases (amends Q-41)

- **Date:** 2026-09-15
- **Question:** How many cases should evaluation runs cover, given the free-tier
  rate limits?
- **Options:** (a) free for development, cheap paid for reported runs;
  (b) free throughout with a scaled-down evaluation; (c) free throughout, full
  214 cases, ~53 days of wall clock; (d) cheap paid throughout.
- **Decision:** (b), scaled to **3 cases**. Paid options to be revisited later.
- **Reason:** User decision, to avoid spend while the machinery is built.
- **⚠️ Consequence, recorded deliberately:** at n = 3 the only attainable
  accuracies are 0, 33, 67 and 100%. Confidence intervals are not meaningful,
  calibration bins will be almost empty, and the panel-vs-single-doctor
  comparison **cannot** reach a conclusion in either direction. Three-case runs
  validate that the harness works end to end; they do not measure anything.
  Q-38's Wilson intervals, paired bootstrap and calibration bins are still
  implemented in full so that scaling up later requires no code change.
- **Case selection:** the 3 cases are drawn deterministically from the dev split
  (D-024), and per Q-41's reasoning should include at least one `dx_in_results`
  case (D-015) and one empty-`Test_Results` case (D-018).

## D-023 — OpenRouter credit tier and request pacing (amends Q-33)

- **Date:** 2026-09-15
- **Question:** Which free-tier daily cap applies, and what concurrency follows?
- **Decision:** User confirms **≥$10 of OpenRouter credits purchased**, so the
  cap is **20 requests/minute and 1000 requests/day**. Q-33's concurrency of 4
  is retained, but a **global token-bucket rate limiter at 18 requests/minute**
  (margin under the 20/min ceiling) is mandatory and shared across all workers.
- **Reason:** The rate limit is per-account, not per-connection, so concurrency
  alone cannot be the control. With LLM calls taking roughly 5–30s, concurrency
  of 3–4 under an 18/min bucket saturates the allowance without breaching it,
  whereas concurrency alone would burst past 20/min immediately.
- **Note:** the judge is exempt from this budget entirely, as it runs on the
  Anthropic SDK (D-021).

## D-024 — `splits.json` generated over all 214 cases now

- **Date:** 2026-09-15
- **Question:** Should splits be generated in full now, or deferred until
  evaluation scales beyond 3 cases?
- **Options:** (a) full splits now, 3-case runs drawn from dev; (b) defer;
  (c) splits over a reduced set.
- **Decision:** (a). `dataset/splits.json` is generated once over all 214 cases
  exactly as Q-36 specifies (dev 40 / heldout 174, seed 20260915, stratified by
  `Correct_Diagnosis`), committed, and the 3 evaluation cases are drawn
  deterministically from dev.
- **Reason:** The brief requires splits be written once and never regenerated.
  Creating them now means scaling to a paid run later needs no new split, and
  nothing tuned on the interim 3 cases can contaminate held-out — which
  deferring would risk.

---

# Round 2 decisions — from the adversarial review (2026-09-15)

Raised by `docs/PLAN_REVIEW.md`; full option analysis in
`docs/DECISIONS_OPEN_2.md`. All nine recommendations accepted.

## D-025 — Challenger and cost-steward are advisory (review #3, Q2-01)

- **Date:** 2026-09-15 · **Options:** (a) advisory only; (b) executive
  (challenger reopens, cost-steward vetoes); (c) split authority.
- **Decision:** (a). Both write opinions into `encounter_log`. The cost-steward
  opines before `order_test` executes and the order proceeds regardless; its
  objection informs subsequent orchestrator decisions. `challenger_final` grants
  the orchestrator exactly **one** re-decision, guarded by
  `challenged_this_finalize: bool`, reset whenever a non-finalize action runs.
- **Reason:** Matches brief §5.3's wording ("argues against", "objects to"),
  which grants neither node control-flow authority. Eliminates the unbounded
  loop of review #1 by construction rather than by a counter. The panel still
  differs from the single doctor in substance: the orchestrator sees both
  opinions before deciding.
- **Implications:** `proposed_action` is removed; a single `action` key is
  written once by the orchestrator. `ChallengerOpinion.should_reopen` and any
  `replacement_action` field are dropped — neither node routes.

## D-026 — Test-selection merged into the orchestrator (review #4, Q2-02)

- **Date:** 2026-09-15 · **Options:** (a) merge and correct the plan's wording;
  (b) add a real `test_selection` node.
- **Decision:** (a). `OrchestratorDecision.expected_information` carries the
  brief's Test-selection output. `PLAN.md` §1.2 is corrected to state
  `single_doctor` = orchestrator + hypothesis + finalize, and that the panel adds
  **challenger and cost-steward**.
- **Reason:** Request budget is the binding constraint (review #13); a separate
  node costs ~25% more panel requests against a 1000/day cap, and the
  orchestrator already produces exactly what brief §5.3 asks Test-selection for.
- **⚠️ Deviation from brief §5.3**, which lists four sub-role nodes. Recorded so
  it is never mistaken for an oversight. Phase 4's acceptance criterion becomes
  "two additional sub-roles", not four.

## D-027 — Rule-based red-flag detection dropped (review #5, Q2-03, amends Q-28)

- **Date:** 2026-09-15 · **Options:** (a) model-reported only; (b) offline in
  the eval runner; (c) inside the encounter graph.
- **Decision:** (a). Red flags are **model-reported only**, logged, never
  stopping the encounter. Q-28's rule-based arm is withdrawn.
- **Reason:** `Vital_Signs` is free text in ~16 formats (`'125/80 mmHg'`,
  `'Within normal range'`, `'Normal for age'`, `'120 bpm (normal for age)'`), so
  the rule-based arm needs an age-aware parser — a clinical-rule design task the
  brief never asked for and warns against as over-engineering. Case 3 is a child
  with HR 120 explicitly annotated "normal for age", which any naive adult
  threshold would misflag. At n=3 (D-022) the comparator it would provide is
  worth nothing. Option (c) was rejected outright: it would read
  `Physical_Examination_Findings` and write to doctor-visible state, handing the
  doctor vitals it never ordered, violating brief §5.2.
- **Note:** if the comparator is wanted later, (b) is the correct shape and can
  be added without touching the encounter graph.

## D-028 — Abstention scoring (review #6, Q2-04)

- **Date:** 2026-09-15 · **Options:** (a) exclude from denominator, report
  coverage; (b) count as incorrect; (c) clinical excluded, parse-failure incorrect.
- **Decision:** (a). Clinical abstention is excluded from the accuracy
  denominator and reported as **coverage**. Parse-failure abstention is recorded
  as outcome `error` and excluded too, consistent with Q-08's treatment of API
  failures. `FinalAnswer.diagnosis` is `""` when abstaining and **the judge is
  not called**. `n_scored`, `n_abstained`, `n_error` and the explicit denominator
  appear beside every accuracy figure.
- **Reason:** Matches brief §9.3's accuracy-vs-coverage framing and keeps a JSON
  formatting failure out of a clinical metric per Q-15. Counting abstention as
  incorrect would punish appropriate uncertainty — the opposite of what the
  Phase 6 abstention experiment studies.

## D-029 — 3-case subset drops the empty-`Test_Results` requirement (review #7, Q2-05, amends D-022)

- **Date:** 2026-09-15 · **Options:** (a) drop it, cover by unit test; (b) force
  the group into dev; (c) try seeds until one lands in dev.
- **Decision:** (a). The 3 evaluation cases are selected deterministically from
  dev as: **the lowest-`case_id` case with `dx_in_results == True`, then the
  lowest-`case_id` remaining cases until 3 are chosen.** The empty-`Test_Results`
  path is covered instead by unit tests over all four such cases (69, 106, 111,
  209).
- **Reason:** Verified that under seed 20260915 the grouped dev split contains
  **zero** empty-`Test_Results` cases, so D-022's constraint was unsatisfiable,
  not merely at risk. The path is deterministic gatekeeper behaviour, which a
  unit test exercises on all four cases every run for free; a single
  non-deterministic LLM encounter proves strictly less. Option (c) is
  seed-shopping on a permanent artefact and was rejected on principle.
- **Note:** dev does contain 5 `dx_in_results` cases, so that half of D-022's
  constraint stands.

## D-030 — Provider pinning (review #20, Q2-06, completes Q-05)

- **Date:** 2026-09-15 · **Options:** (a) pinned always, abort on outage;
  (b) pinned for reported runs, fallbacks for development; (c) fallbacks always.
- **Decision:** (b). `provider: {order: ["Nvidia"], allow_fallbacks: false}` for
  any run that writes a report; fallbacks permitted for `--cache` / `--no-report`
  development runs. Which applied is recorded in run metadata.
- **Reason:** Verified live that `nvidia/nemotron-3-super-120b-a12b:free` has
  exactly **one** endpoint — provider `Nvidia`, 262k context, advertising
  `tools`, `structured_outputs` and `response_format` — so there is no ordering
  choice to make, only a behaviour on unavailability. Measurement stays strict;
  development stays resilient.

## D-031 — Judge calibration set (review #21, Q2-07)

- **Date:** 2026-09-15 · **Options:** (a) ~40 synthetic pairs; (b) ~40 real dev
  encounters; (c) defer.
- **Decision:** (a). A hand-labelled set of ~40 synthetic
  (final answer, ground truth) pairs, built from real `Correct_Diagnosis` values
  plus plausible near-misses — synonyms, broader/narrower terms, abbreviation
  variants (`(PML)`, `(LCPD)`), and confident wrong answers — stored in
  `config/judge_calibration.yaml`. **Bar: exact agreement ≥ 90% or Cohen's
  κ ≥ 0.8.** Below the bar, the judge prompt is revised and the set re-run
  before any accuracy figure is quoted.
- **Reason:** Validates the judge now, costs **zero** OpenRouter requests (the
  judge is on the Anthropic SDK, D-021), and deliberately covers the hard cases
  that 3 real encounters never would. Option (b) would consume ~2 days of the
  daily request budget that D-022 exists to conserve.
- **Also decided (review #21 secondary):** subscription auth exposes no per-call
  price, so R5's "judge cost" becomes **judge token and call counts**, and a
  per-run judge **call cap** is added.

## D-032 — Phase 2 gains the graph skeleton (review #22, Q2-08)

- **Date:** 2026-09-15 · **Options:** (a) pull the skeleton into Phase 2;
  (b) non-graph REPL, `interrupt()` deferred to Phase 3.
- **Decision:** (a). Phase 2 delivers `EncounterState`, `route_action`,
  `should_continue` and a **human-driven** orchestrator using `interrupt()`.
  Phase 3 adds only the LLM orchestrator, hypothesis and finalize nodes.
- **Reason:** Honours Q-31's choice of the graph-based interrupt mechanism, and
  driving the real state machine by hand surfaces design faults before LLM
  non-determinism can hide them. Option (b) is Q-31's explicitly rejected option.

## D-033 — Free-endpoint data policy wording (review #23, Q2-09)

- **Date:** 2026-09-15 · **Options:** (a) check the account setting and correct
  the plan's wording; (b) disable training-permitting providers; (c) ignore.
- **Decision:** (a). `PLAN.md` §6.4 now reads "no LangSmith; note that free
  OpenRouter endpoints are governed by the account's free-model data policy,
  which may permit provider-side logging". **User to confirm the setting on
  their OpenRouter account page.**
- **Reason:** Verified that OpenRouter maintains *separate data-policy settings
  for free and paid models*, but their documentation does not state whether free
  endpoints require permitting training — so the plan must not assert either
  way. Brief §2 is not violated regardless: the data is public MIT-licensed
  benchmark text, not real patient data. Option (b) risks making the chosen
  model unusable, as it has exactly one provider (D-030).

---

# Round 3 decisions — from adversarial review round 2 (2026-09-15)

Raised by `docs/PLAN_REVIEW_2.md`. All four recommendations accepted.

## D-034 — Fuzzy matching tier dropped (review B-10b, amends Q-11)

- **Date:** 2026-09-15 · **Options:** (a) drop the tier; (b) keep at 85 and tune
  later; (c) keep and build a labelled tuning set.
- **Decision:** (a). The gatekeeper cascade becomes **exact (normalised) →
  curated synonym table → LLM fallback over key names only**. `rapidfuzz` is
  removed from the dependency list (amends Q-35).
- **Reason:** The threshold had no value, no labelled set to tune against, and
  no stated objective — and the two candidate objectives trade off directly:
  maximising match rate increases wrong-key matches, and a wrong-key match hands
  the doctor results it never ordered, which brief §5.2 explicitly prohibits.
  "Tuned on dev only" also protected nothing here, since every Phase 3–5 run is
  on dev (review B-10). Cost: the LLM tier fires more often.

## D-035 — Ambiguous leaf requests are disambiguated by the LLM tier (review B-13)

- **Date:** 2026-09-15 · **Options:** (a) LLM tier picks one parent; (b) return
  all matching parents; (c) treat as unmatched.
- **Decision:** (a). When a requested analyte appears under more than one
  top-level key, the LLM tier selects the intended parent from the key names it
  already sees, and the gatekeeper returns **that one sub-tree**. The match tier
  is logged as `llm_disambiguated`.
- **Reason:** Verified that in **58 of 214** cases a leaf name appears under more
  than one top-level key, and in **9** the duplicated leaf is a real orderable
  analyte — `WBC` under both `Complete_Blood_Count` and `Urinalysis` (cases 10,
  38, 55, 80, 101), `Na`/`Glu` under serum and urine (29), `Bilirubin` under
  blood and urine (58), `Glucose` under labs and urine (66), `WBC` under CBC and
  a joint aspirate (30). Option (b) was rejected because returning every matching
  panel discloses tests the doctor never ordered (brief §5.2). Option (c) turns
  well-posed clinical questions into fabricated unavailability and burns a turn.

## D-036 — Judge calibration protocol (review B-11, amends D-031)

- **Date:** 2026-09-15 · **Options for labelling:** (a) Claude drafts, user
  reviews and corrects; (b) user labels all 40; (c) Claude labels alone.
- **Decision:** (a) for labelling, **plus** the review's structural fixes, which
  are accepted regardless:
  - The ~40 pairs are split into a **20-pair tuning half** and a **20-pair sealed
    half**, fixed before any judge prompt is written.
  - Judge prompt revision is permitted **only** against the tuning half.
  - The bar is a **conjunction**: exact agreement ≥ 90% **and** Cohen's κ ≥ 0.8,
    quoted on the sealed half and measured **once**.
  - Labels are committed before the judge prompt is finalised. The user's
    corrections are the ground truth.
- **Reason:** As originally written the procedure was "iterate the judge prompt
  until it passes on the 40 pairs you are grading it with" — fitting the judge to
  its own validation set, the same failure mode §6.1 prevents one level up. The
  disjunctive bar was also weaker than either threshold alone, and 90% raw
  agreement is attainable on a skewed label set by a judge that fails exactly on
  the `synonym`/`broader`/`narrower` boundary Q-22 says will be doing real work.

## D-037 — Judge per-run call cap = 200 (review B-10a, completes D-031)

- **Date:** 2026-09-15 · **Options:** 200 / 50 / 1000.
- **Decision:** **200 calls per run.**
- **Reason:** Realistic need is 12 (3 cases × 4 configurations) plus ~40
  calibration pairs, so 200 never fires in normal operation and a breach means
  something is looping — which is what the cap is for. It is the only limit of
  any kind on the judge, which runs on subscription auth with no price signal.

---

# Round 4 decisions — from adversarial review round 3 (2026-09-15)

## D-038 — The challenger does not run before a cap-forced finalize (amends Q-16)

- **Date:** 2026-09-15
- **Question:** Review C-1 showed `challenger_stop` — the node that ran the
  challenger on the turn/spend/request-cap path — produced a `ChallengerOpinion`
  that nothing read. `finalize` reads `differential` and `summary`, not the
  opinion, so the node could not affect any output.
- **Options:** (a) delete the node and exempt the cap path from Q-16; (b) wire
  the opinion into `finalize`'s prompt so it can influence the final answer.
- **Decision:** (a). `challenger_stop` is deleted. Q-16's "challenger before
  every `finalize`" now holds on the **voluntary** finalize path only.
- **Reason:** On the cap path the budget is already spent, which is why the
  encounter is ending; spending 1–3 further calls on an opinion that arrives too
  late to change the differential is pure cost. Option (b) would have given the
  challenger influence over the final answer without a re-deliberation, which is
  a larger change to D-025's advisory model than the situation warrants.
- **⚠️ Amends Q-16.** Recorded so the exemption is never mistaken for a bug.

## D-039 — The OpenRouter client owns the authoritative spend total (review C-9)

- **Date:** 2026-09-15
- **Question:** Revision 3 had two spend accumulators — a running total inside
  the client (which raises `BudgetExceeded`) and `EncounterState.spend_usd` with
  an `operator.add` reducer — with no stated reconciliation, so `spend_cap` and
  `budget_exhausted` raced for the same event and the reducer risked
  double-counting.
- **Options:** (a) the client owns the total and raises; state holds a snapshot;
  (b) state owns the total and `check_stop` tests it.
- **Decision:** (a), which is the reviewer's own recommendation.
  `EncounterState.spend_usd` becomes a **last-write snapshot** refreshed by
  `check_stop`, never the value the cap is tested against.
- **Reason:** Only the client sees every call, including those made inside
  `ask_patient`, `hypothesis` and the gatekeeper's LLM tier. A state-held total
  necessarily lags by at least one node and would let the cap be exceeded
  silently. This also removes the `add`-reducer double-count risk.
- **Note:** Applied without a separate user question. It is a mechanical
  implementation choice with one clearly correct answer and the review
  recommended it explicitly; flagged here so it can be reversed if the user
  disagrees.

---

# Phase 3 decisions (2026-09-15)

Arising during implementation and from `docs/PHASE_3_REVIEW.md`.

## D-040 — `parse_failure` is a stop reason; `spend_cap` is not reachable in state

- **Date:** 2026-09-15
- **Question:** Q-15 requires a forced finalize after the repair budget, kept
  distinct from a clinical abstention. Where does that distinction live?
- **Decision:** `StopReason` gains **`parse_failure`**, written by the
  orchestrator. `stop_reason` therefore crosses the decision subgraph boundary
  and is declared in `DecideOutput`. `spend_cap` remains in the literal but is
  **unreachable in practice**: a spend breach surfaces as `BudgetExceeded` and
  is recorded as `budget_exhausted`, because the breach is detected inside a
  call rather than by `check_stop`'s post-hoc comparison.
- **Reason:** Without a distinct reason the runner cannot separate a JSON
  formatting failure from clinical uncertainty, and D-028 requires exactly that
  separation — one is outcome `error`, the other `abstained`. At n=3 the
  difference is 33 accuracy points.
- **⚠️ Amends** PLAN.md §4.1's `StopReason` literal and §2.3's subgraph output
  schema, both written before the repair loop existed.

## D-041 — `EncounterSummary.findings` is written by the model, not copied from the log

- **Date:** 2026-09-15
- **Question:** PLAN.md §4.1 said `findings` is "derived from `encounter_log` by
  the hypothesis node". The first implementation read that as *copied* — each
  finding was `f"{actor}: {text}"` straight from the transcript.
- **Decision:** `HypothesisUpdate` gains a **`findings`** field. The model writes
  a short summary in its own words; the prompt states that later turns see the
  summary rather than the exchange. `tests_ordered` stays mechanical, because
  those are the doctor's own requests and carry no result text.
- **Reason:** Copying defeated Q-29 completely. The decision subgraph's schema
  genuinely excludes `encounter_log`, so the structural isolation held — but the
  orchestrator was reading the transcript *through* the summary. For the 29
  cases where the dataset embeds the diagnosis in test results, that is the
  difference between the doctor seeing it once and on every subsequent turn,
  which is precisely the concern behind review finding B-7.
- **Caught by:** `test_orchestrator_prompt_contains_no_raw_event_text`, written
  before the bug was known to exist.

## D-042 — `.env` loading

- **Date:** 2026-09-15
- **Question:** Keys exported in an interactive shell are not visible to the
  tooling, which spawns fresh shells. How are secrets supplied?
- **Decision:** The CLI loads a git-ignored `.env` at startup, with
  `.env.example` committed (brief §4). **Real environment variables always take
  precedence**, so an explicit export is never overridden by a stale file. Only
  variable *names* are echoed, never values.
- **Reason:** The brief requires `.env.example`; this makes it functional rather
  than decorative, and keeps secrets out of both the repository and the logs.

## D-043 — Agent model switched to `inclusionai/ling-3.0-flash-vl:free` (supersedes D-020)

- **Date:** 2026-09-15
- **Question:** `nvidia/nemotron-3-super-120b-a12b:free` passed the Phase 1 spike
  on a flat schema but performed badly under load. Is there a better free model?
- **Measured, live, against the real `HypothesisUpdate` (nested differential):**

  | model | success | median | note |
  |---|---|---|---|
  | `inclusionai/ling-3.0-flash-vl:free` | **2/2** | **10.0s** | 8 dx, nested objects intact |
  | `dots-studio/dots-3-note-preview:free` | 2/2 | 14.8s | top-1 probability 1.0 — poorly calibrated |
  | `nvidia/nemotron-3-super-120b-a12b:free` | **0/2** | 75.1s | empty responses |
  | `nex-agi/nex-n2.5-pro:free` | 0/2 | 150s | timeout |

  A full 6-turn encounter on nemotron needed **17 empty-response backoffs across
  37 calls** — 46% of calls refused — and crashed outright before backoff existed.
- **Decision:** switch every OpenRouter role to
  **`inclusionai/ling-3.0-flash-vl:free`**, provider pinned to **Novita** (its
  only endpoint), with structured output by **forced tool call**.
- **Reason:** It is roughly 4× faster on a flat schema (4s vs 15s), held the
  nested schema where nemotron failed completely, and showed no load-shedding.
  Its clinical output was also sensible — "paraneoplastic cerebellar
  degeneration" as the leader for ataxia with a smoking history.
- **⚠️ Mechanism change.** This model has `tools` and `tool_choice` but **no**
  `structured_outputs` and **no** `response_format`. Schemas are therefore
  enforced by a forced tool call, not a native strict schema. That is the second
  rung of the Q-15 fallback ladder, now the primary mechanism, declared in
  `config/models.yaml` under `structured_output.method` and recorded per run.
- **Rejected alternatives the user asked about:**
  - `thinkingmachines/inkling:free` and `inkling-small:free` — **HTTP 403**,
    `failed_routing_step: "Gate Free Endpoints by Agentic Harness"`. Restricted
    to allow-listed client apps; unreachable from our own API client.
  - `z-ai/glm-5.2:free` — reachable but 32k context (vs 1M paid), **no tools, no
    response_format, no structured outputs**, so prompt-and-parse only; returned
    HTTP 503 when tested.
- **Supersedes:** D-020 (model choice) and the provider half of D-030.

## D-044 — Hard wall-clock deadlines on every call and every case

- **Date:** 2026-09-15
- **Question:** A 3-case run made no progress for **65 minutes** while the
  process stayed alive. Diagnosis (from `lsof` + `sample`): two ESTABLISHED
  sockets to OpenRouter and an event loop pumping an async generator — the
  provider was trickling bytes on an open stream.
- **Root cause:** `httpx` timeouts are **per-operation**, not total. A read
  timeout fires only after that long with *no* data arriving; a slow trickle
  resets the timer indefinitely. The design bounded **turns** (Q-24) and
  **spend** (Q-25) and **requests** (review #13) — but nothing bounded
  **wall-clock time**, and on a free model the spend cap is inert anyway.
- **Decision:** three changes.
  1. `LLMCaller` wraps every call in `asyncio.wait_for(..., call_timeout)` — a
     hard total deadline, default `budgets.timeout_seconds`.
  2. `ChatOpenAI` is constructed with **`max_retries=0`**. SDK-internal retries
     would multiply the deadline by `(max_retries + 1)` invisibly; the repair
     loop owns retries so the ceiling is knowable.
  3. `run_case` accepts `case_deadline_s` as defence in depth, set to
     `max_turns × 6 × timeout_seconds`. A case that cannot finish in that long
     is stuck, and one stuck case must not hold up a run.
- **Also:** a timeout is classified as a **transient** failure, not a content
  failure — it backs off and resends rather than re-prompting. Re-prompting a
  provider that is not answering achieves nothing.
- **Reason:** without a total deadline the turn cap is not a stop condition at
  all in wall-clock terms, which contradicts PLAN.md §5's claim that every
  encounter terminates. Regression tests cover both the case deadline and the
  timeout classification.

## D-045 — Gatekeeper matches by token containment (extends Q-11, D-034)

- **Date:** 2026-09-15
- **Question:** A live 3-case run resolved **14 of 17** test requests through the
  LLM tier and **zero** through exact or synonym matching. The 141-alias table
  never fired once.
- **Cause:** tiers 1 and 2 required equality after normalisation. Case keys are
  terse (`MRI_Brain`, `Complete_Blood_Count`, `Electroencephalogram`) while a
  doctor asks in clinical language — "MRI of the brain with contrast", "CBC with
  differential", "complete blood count with differential". Every qualifier
  defeated the match.
- **Decision:** add **token containment**: a key matches when all of its
  normalised tokens appear in the request. Applied to case keys (tier
  `contains`) and to synonym aliases, so "CBC with differential" canonicalises
  through the alias fragment. Ambiguity is resolved by preferring the key with
  more tokens, and a tie falls through to the LLM tier rather than guessing.
- **Why this is not the fuzzy tier D-034 removed:** there is **no threshold**.
  Containment is exact set inclusion — deterministic, explainable, and directional:
  `{mri, spine}` is not a subset of `{mri, of, the, brain}`, so a request for a
  brain MRI can never return a spine MRI.
- **Measured:** 8 of 10 realistic phrasings now resolve deterministically where
  0 did before, with genuinely absent tests still correctly unmatched.
- **Also:** the synonym table gained `electroencephalogram` (**EEG had no entry
  at all** — only `electrocardiogram`/ECG, a different test), plus mammography,
  nerve conduction, creatinine, CRP and ESR. The gatekeeper now records the
  request text in the trace, because this gap was invisible without it.
