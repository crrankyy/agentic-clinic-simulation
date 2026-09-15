# Build "AgentClinic-from-scratch" with LangGraph + OpenRouter

## 0. Ground rules (read first, follow throughout)

### 0.1 No assumptions. Ever.
This is the most important rule in this document.

- **Do not make assumptions.** Whenever anything is unclear, underspecified,
  has more than one reasonable interpretation, or requires a choice I have not
  explicitly made, **stop and ask me** before proceeding.
- This applies to everything: architecture, library versions, model choices,
  numeric limits, file names, prompt wording, error-handling behavior, test
  scope, and interpretations of this document.
- When asking, use your ask-user/question tool if available; otherwise stop and
  ask in chat. For each question give:
  1. the question,
  2. why it matters,
  3. 2–4 concrete options with trade-offs,
  4. your recommendation, clearly labeled as a recommendation, not a decision.
- **Batch questions** where possible so I can answer several at once, but never
  skip a question to avoid interrupting me.
- Do not proceed on a recommendation until I have confirmed it.
- Record every answer in `docs/DECISIONS.md` (date | question | options |
  my decision | reason). Before asking something, check this file; do not ask
  again what is already decided. If a later situation seems to conflict with a
  recorded decision, ask me rather than reinterpreting it.
- Values written in this document (e.g., case counts, field names) are
  requirements or facts to verify, not defaults to fill gaps. Anything this
  document does not specify must be asked about.
- If you notice you are about to write "assume", "default to", "probably", or
  "for now" in code, docs, or reasoning, stop and turn it into a question.

### 0.2 Other rules
- This is a **learning project**. Code should be readable and explained.
- Agents and orchestration are built with **LangGraph**. Prefer explicit
  `StateGraph` construction (nodes, edges, conditional edges, subgraphs) over
  prebuilt agent helpers, unless I approve otherwise.
- LLMs are called through **OpenRouter**, not the Anthropic SDK or any
  provider-specific SDK.
- Do not copy or import code from the original AgentClinic repository
  (github.com/SamuelSchmidgall/AgentClinic). We only use its dataset files.
- LangGraph, LangChain, and OpenRouter APIs change over time. **Consult their
  current official documentation** before using an API, and do not rely on
  memory for signatures. If the docs are unclear or conflict with each other,
  ask me.
- Work in **phases** (Section 10). Do not start a phase until the previous one
  is finished, tested, and approved by me.
- Before writing implementation code, complete Section 1.

---

## 1. Mandatory planning and adversarial review (before implementation)

### Step 1: Download and inspect the dataset
The `dataset/` directory does not exist yet. Before downloading, confirm with me
the approach (e.g., a download script vs a one-off command, and whether to pin
to a specific commit). Then follow Section 3 to download and verify the files,
and inspect them with small Python snippets: case counts, key structure, and
variation across cases. If the files disagree with Section 3, report the
discrepancy and ask me how to proceed.

### Step 2: Collect decisions
Go through Sections 2–12 and list every point that needs my decision. Ask me in
batches (Section 0.1). Record answers in `docs/DECISIONS.md`. At minimum this
includes the items marked **[ASK]** in this document.

### Step 3: Write `docs/PLAN.md`
It must contain:
1. Architecture overview, including the **LangGraph graph design**: every graph
   and subgraph, their state schemas, nodes, edges, conditional routing
   functions, and entry/exit points. Include Mermaid diagrams.
2. A visibility matrix: rows = agents/nodes, columns = case fields, showing what
   each can and cannot access, and **how that is enforced in code**.
3. The action protocol and JSON schemas for all structured outputs.
4. Episode loop, stop conditions, and budgets (values from DECISIONS.md).
5. Evaluation design: splits, metrics, storage.
6. The phase plan from Section 10 with concrete tasks and acceptance criteria.
7. A **Decisions** section linking to `docs/DECISIONS.md`. The plan must
   contain **no assumptions**; every choice must trace to a recorded decision or
   to an explicit requirement in this document.
8. An **Open Questions** list (must be empty before implementation begins).
9. A **Risks** list.

### Step 4: Adversarial review
Launch a **separate subagent with fresh context** (via your Task/subagent tool)
acting as a skeptical senior engineer and clinical-AI evaluator. Give it only
`docs/PLAN.md`, `docs/DECISIONS.md`, this prompt, and read access to `dataset/`.
Its instructions:

> Your job is to find problems, not to approve. Attack this plan. Look for:
> - **Hidden assumptions:** any choice in the plan that does not trace to
>   `docs/DECISIONS.md` or an explicit requirement in the prompt. Every such
>   item is a **blocker**.
> - **Ambiguity:** anything two engineers could implement differently. Name
>   both interpretations.
> - Any path by which `Correct_Diagnosis` (or text that effectively reveals it)
>   could reach a doctor-side agent: via LangGraph shared state, the
>   checkpointer, subgraph state propagation, the gatekeeper, the patient, the
>   evidence agent, traces fed back into context, or error messages.
> - Isolation enforced only by prompting instead of by code and state design.
> - LangGraph design issues: state keys that leak between subgraphs, reducers
>   that could duplicate or drop messages, missing recursion limits, routing
>   functions with unhandled return values, checkpoint data containing hidden
>   case fields.
> - OpenRouter issues: models that may not support structured output or tool
>   calling, missing retry/backoff, provider fallback behavior that changes the
>   model silently, unclear cost accounting.
> - Mismatches between the plan and the actual dataset (verify by reading it).
> - Ambiguous responsibilities between agents or nodes.
> - Missing stop conditions, infinite loops, missing cost/turn caps.
> - Evaluation flaws: tuning on held-out data, non-deterministic splits,
>   gameable metrics, an unvalidated judge.
> - Unhandled edge cases: unlisted tests, malformed model output, API errors,
>   rate limits, blank dataset lines, failed downloads.
> - Over-engineering that does not serve the learning goal.
> - Anything in the prompt that the plan ignored or contradicted.
>
> Output a numbered list of issues. For each: severity (blocker / major /
> minor), the plan section, the problem, and a concrete fix **or** the question
> that must be put to the user.

Write the output verbatim to `docs/PLAN_REVIEW.md`.

### Step 5: Resolve
For every issue, append a response in `docs/PLAN_REVIEW.md`:
`accepted (what changed)`, `rejected (reason)`, or `needs user decision`.
**Do not resolve "needs user decision" items yourself.** Ask me, record the
answer in `docs/DECISIONS.md`, then update the plan. If any blocker remains, run
another review round with a fresh subagent.

### Step 6: Stop and wait
Present: a plan summary, the key review findings and resolutions, and any
remaining questions. **Do not write implementation code until I approve.**

Run a lighter adversarial review (fresh subagent, focused on the diff, hidden
assumptions, and information leakage) at the end of Phases 3, 4, and 5.

---

## 2. Project goal

Simulate a clinical encounter in which a panel of doctor agents must diagnose a
patient whose case is hidden from them. Doctors gather information by
questioning a patient agent, requesting physical exam findings, ordering tests
through a gatekeeper agent, and consulting the literature through an evidence
agent. A judge agent scores the final diagnosis against ground truth. Every run
produces metrics I can compare across configurations.

This is an educational simulation only. It must never be presented as medical
advice and must never process real patient data. Put this disclaimer in the
README and in the CLI startup output.

---

## 3. Dataset (must be downloaded)

Source: the AgentClinic GitHub repository (MIT-licensed). Files are at:

```
https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/<file>
```

| File | Expected cases | Use |
|---|---|---|
| `agentclinic_medqa_extended.jsonl` | 214 | **Primary dataset** |
| `agentclinic_medqa.jsonl` | 107 | Optional smaller set |
| `agentclinic_nejm_extended.jsonl` | 120 | Optional later multimodal phase |
| `agentclinic_nejm.jsonl` | 15 | Not planned for use |

**[ASK]** which files to download, and whether to pin to a specific commit SHA
(obtainable with `git ls-remote https://github.com/SamuelSchmidgall/AgentClinic.git`).

Download requirements:
- Save into `dataset/` (create it).
- Skip files that already exist and match the recorded checksum; otherwise
  re-download. **[ASK]** what to do if an existing file's checksum differs.
- Verify non-blank line counts against the table above. On mismatch, stop and
  report to me.
- Write `dataset/MANIFEST.json` with file name, source URL, commit SHA,
  download time, SHA-256, and case count.
- Handle network errors with clear messages; do not leave partial files.
- **[ASK]** whether `dataset/` should be git-ignored.

### MedQA files (primary)
One JSON object per line with a single top-level key `OSCE_Examination`
containing:
- `Objective_for_Doctor` (str)
- `Patient_Actor` (dict): `Demographics`, `History`, `Symptoms`
  (`Primary_Symptom`, `Secondary_Symptoms`), `Past_Medical_History`,
  `Social_History`, `Review_of_Systems`
- `Physical_Examination_Findings` (dict): nested; keys vary by case
- `Test_Results` (dict): nested; **keys vary per case**. Common categories
  include `Complete_Blood_Count`, `Imaging`, `Blood_Tests`, `Urinalysis`,
  `Skin_Biopsy`, `Chest_X-ray`, `Echocardiogram`, `Laboratory_Tests`.
  Imaging appears as text reports.
- `Correct_Diagnosis` (str): free-text ground truth

Verify all of this against the downloaded files.

Loader requirements:
- Parse into typed pydantic models; nested exam/test dicts stay as generic
  nested mappings.
- Skip blank lines (the last line may lack a trailing newline).
- Stable case IDs. **[ASK]** the ID scheme.
- **[ASK]** how to handle malformed records (skip and report, or fail).

### NEJM files (later phase only)
Flat keys: `image_url`, `question`, `patient_info`, `physical_exams`, `answers`
(list of `{text, correct}`), `type`. Images are not included; `image_url`
points to NEJM servers. The `question` field often describes findings and could
leak the answer, so it must never reach doctor agents unfiltered.

---

## 4. LLM access via OpenRouter

- OpenRouter exposes an OpenAI-compatible Chat Completions API at
  `https://openrouter.ai/api/v1`, authenticated with
  `Authorization: Bearer $OPENROUTER_API_KEY`. Verify this and all details below
  in the current OpenRouter docs.
- **[ASK]** the client approach, presenting options such as: LangChain's
  `ChatOpenAI` pointed at the OpenRouter base URL, a dedicated OpenRouter
  LangChain integration if one currently exists, or our own thin `httpx` client
  wrapped for use in LangGraph nodes. Explain the trade-offs for learning,
  structured output, and cost tracking.
- **[ASK]** which model to use for each role (patient, gatekeeper, each doctor
  sub-role, evidence, judge). To help me choose, fetch the live model list from
  `https://openrouter.ai/api/v1/models` and show candidates with context length,
  prices, and whether they support tool calling / structured outputs.
- Model IDs must live in config, never hard-coded.
- **[ASK]** whether to allow OpenRouter provider fallbacks/routing, or to
  require a fixed provider, since fallbacks can change behavior between runs.
- **[ASK]** optional attribution headers (`HTTP-Referer`, `X-Title`): use them
  or not, and with what values.
- **Structured output:** not every model supports JSON schema or tool calling.
  Before relying on it for a role, verify support for the chosen model. If a
  chosen model lacks support, ask me how to proceed.
- **Cost tracking:** check the current OpenRouter docs for how per-request
  usage and cost are reported, and **[ASK]** which method to use.
- Retries with exponential backoff on rate limits and 5xx errors, plus
  timeouts. **[ASK]** retry counts and timeout values.
- Secrets only from environment variables (`OPENROUTER_API_KEY`, optional
  `NCBI_API_KEY`). Never log them. Provide `.env.example`.

---

## 5. Agents (LangGraph nodes and subgraphs)

**Information isolation must be enforced by the state design, not by prompts.**
`Correct_Diagnosis` and the hidden case sections must never be written into any
LangGraph state channel that doctor-side nodes can read, and must never be
stored in checkpoints of the encounter graph. Present me the options for
achieving this (e.g., a case store outside graph state accessed through
restricted views injected via config/closures, separate subgraph state schemas
with explicit input/output schemas) and **[ASK]** which one to use.

### 5.1 Patient agent
- Sees: `Patient_Actor` only.
- Answers in plain, lay, first-person language. Never states or hints at a
  diagnosis. Answers only what is asked.
- Must not invent findings. **[ASK]** exactly how it should answer questions
  about things not in its case file.

### 5.2 Gatekeeper (measurement) agent
- Sees: `Physical_Examination_Findings` and `Test_Results` only.
- Maps a doctor's request to matching entries and returns only those results,
  without interpretation. Never reveals unordered results.
- Must handle synonyms and abbreviations (e.g., "CBC" → `Complete_Blood_Count`).
  **[ASK]** the matching strategy (deterministic/fuzzy first with LLM fallback,
  LLM only, etc.), and log which method matched.
- **[ASK]** what to return when a requested test or exam is not in the case
  (e.g., "normal", "not available", or something else). Log every such event
  as an unlisted-test event.
- Charges a cost per test from `config/test_costs.yaml`. **[ASK]** the price
  table source and the price for unknown tests.

### 5.3 Doctor panel (subgraph)
- Sees: `Objective_for_Doctor` plus information obtained during the encounter
  only.
- Sub-role nodes:
  - **Hypothesis:** ranked differential with probabilities and rationales.
    **[ASK]** maximum list length.
  - **Test-selection:** proposes the most informative next action and what
    result would change the differential.
  - **Challenger:** argues against the leading diagnosis and names the most
    dangerous alternative not yet ruled out.
  - **Cost-steward:** objects to tests unlikely to change management.
- **Orchestrator node + conditional edge:** each turn chooses one action:
  `ask_patient`, `request_exam`, `order_test`, `search_literature`, `finalize`.
  Output is validated with pydantic. **[ASK]** the retry policy on invalid
  output and what happens after retries are exhausted.
- **[ASK]** when the challenger and cost-steward run (every turn, every N turns,
  before finalize, or another schedule).
- Final output schema: `diagnosis`, `differential`, `confidence`, `abstain`,
  `red_flag`, `rationale`. **[ASK]** to confirm or change this schema.

### 5.4 Evidence agent
- Sees: only the query from the doctor panel, never the case.
- Tools: PubMed via NCBI E-utilities (`esearch`, `esummary`/`efetch`) and the
  openFDA drug label endpoint, implemented as LangChain/LangGraph tools.
- Respect NCBI rate limits (verify current limits in NCBI docs).
  **[ASK]** the `tool` and `email` values for NCBI requests.
- Cache HTTP responses on disk. **[ASK]** cache location and expiry.
- Every claim must cite a PMID or openFDA record from the retrieved set;
  validate in code and **[ASK]** whether uncited/invalid citations should be
  stripped or flagged.

### 5.5 Judge agent
- Sees: the final diagnosis (and differential) plus `Correct_Diagnosis`.
- Runs **outside** the encounter graph (separate graph or node that only
  executes after the encounter state is finalized). **[ASK]** which.
- Output: `correct`, `match_type` (`exact|synonym|broader|narrower|wrong`),
  `reasoning`. **[ASK]** whether "broader" and "narrower" count as correct.
- Also computes top-k accuracy over the differential. **[ASK]** the value(s)
  of k.
- Calibration in Phase 5: a hand-labeling CLI and an agreement report.

---

## 6. Encounter graph behavior

1. Load case → build restricted views (Section 5).
2. Doctor panel receives `Objective_for_Doctor`.
3. Loop: orchestrator chooses action → routed to patient / gatekeeper /
   evidence node → result appended to doctor-side state → hypothesis node
   updates → challenger / cost-steward per the agreed schedule.
4. Stop conditions: `finalize`, max turns, max spend per case, and a confidence
   threshold rule. **[ASK]** all numeric values and whether the confidence rule
   should exist at all. On forced stop, the panel must still produce a final
   answer.
5. Set an explicit LangGraph recursion limit. **[ASK]** its value relative to
   max turns.
6. **Red-flag tracking:** record the turn at which the panel first raises a
   red flag. **[ASK]** whether red-flag detection is model-reported only or
   also rule-based, and what it should do (log only, or stop the encounter).
7. **Context management:** the doctor panel uses a structured encounter summary
   rather than replaying all raw messages. **[ASK]** the summarization
   approach and output truncation limits.
8. **Checkpointing:** **[ASK]** whether to use a LangGraph checkpointer (e.g.,
   in-memory or SQLite) and what it is used for (resume, replay, debugging).
   Checkpoints must contain no hidden case fields.
9. **Human-in-the-loop:** the Phase 2 interactive mode, where I play the doctor,
   should use LangGraph's interrupt mechanism if appropriate. **[ASK]** to
   confirm.

---

## 7. Infrastructure

- **Tracing:** every LLM call, tool call, and node transition written to
  `runs/<run_id>/traces/<case_id>.jsonl` (timestamps, node, inputs, outputs,
  tokens, latency, cost), plus a CLI trace viewer using `rich`.
  **[ASK]** whether to also enable LangSmith tracing (it sends data to an
  external service).
- **Spend guard:** hard caps per case and per run. **[ASK]** the values.
- **Concurrency:** async execution with a semaphore. **[ASK]** the
  concurrency level, considering OpenRouter rate limits for the chosen models.
- **Response cache** for development runs. **[ASK]** whether to build it and
  whether it is allowed during evaluation.
- **Config:** YAML in `config/` plus CLI overrides. Prompts live in
  `config/prompts/` as editable files.
- **Dependencies:** **[ASK]** the package manager (e.g., `uv`, `pip`,
  `poetry`), Python version, and the final dependency list. Expected core:
  `langgraph`, `langchain-core`, an OpenRouter-compatible chat client per the
  decision in Section 4, `pydantic`, `httpx`, `pyyaml`, `rich`, a CLI library,
  `rapidfuzz` (if fuzzy matching is chosen), `pytest`, `pytest-asyncio`.
  Pin versions in `pyproject.toml` once decided.

---

## 8. Evaluation

- **Splits:** deterministic, seeded, saved once to `dataset/splits.json`, never
  regenerated silently. **[ASK]** split sizes and seed. The held-out split
  runs only with an explicit `--split heldout` flag.
- **Per-case metrics:** judge correctness, top-k correctness, turns, patient
  questions, tests ordered, unlisted tests, test cost, tokens, API cost,
  latency, abstained, red-flag turn, final confidence.
- **Report:** `runs/<run_id>/report.md` + `results.csv`, including accuracy
  with confidence intervals, calibration bins, and a breakdown by common test
  categories. **[ASK]** the confidence-interval method and number of
  calibration bins.
- **Configurations:** `single_doctor` vs `panel`, each with/without the
  evidence agent, switchable by CLI flags. The `single_doctor` mode should be a
  separate, simpler graph so the comparison is clean. **[ASK]** to confirm.
- **Run comparison:** a command that diffs two run reports.

---

## 9. Experiments (after the core works)

1. **Leakage probe:** scripted adversarial questions to the patient; measure
   how often replies reveal the diagnosis (judge-checked). **[ASK]** the
   question set.
2. **Demographic counterfactuals:** swap sex and/or age where clinically
   irrelevant. **[ASK]** how cases are selected and who approves the list.
3. **Abstention:** allow abstaining; report accuracy vs coverage.
4. **Unlisted-test sensitivity:** compare the gatekeeper behaviors I choose
   between.

---

## 10. Phases

Each phase ends with passing tests, `docs/PHASE_N_NOTES.md` (what was built,
decisions made, anything surprising), exact run commands, and a stop for my
review. New questions that come up mid-phase must be asked immediately, not
batched until the end.

0. **Phase 0: dataset.** Download script, verification, manifest, inspection
   report.
1. **Phase 1: foundations.** Project skeleton, config, loader, splits,
   OpenRouter client layer, tracing, fake LLM for tests. No real API calls in
   tests.
2. **Phase 2: patient + gatekeeper.** Both nodes plus an interactive mode
   where I play the doctor. Tests for gatekeeper matching and visibility.
3. **Phase 3: single doctor graph + judge + eval harness.** End-to-end on a
   small dev subset (**[ASK]** size). Adversarial review round.
4. **Phase 4: doctor panel subgraph.** Sub-roles and orchestrator; compare
   against single doctor. Adversarial review round.
5. **Phase 5: evidence agent + judge calibration.** Adversarial review round.
6. **Phase 6: experiments** from Section 9.
7. **Phase 7 (optional): NEJM multimodal.** Only if I request it; ask me about
   image handling, vision-capable models on OpenRouter, and question filtering
   before starting.

---

## 11. Testing requirements

- A fake chat model that returns scripted responses and plugs into the same
  interface the graphs use, so all graphs run offline and deterministically.
- **Leakage tests (required):** assert that `Correct_Diagnosis`
  (case-insensitive) never appears in any doctor-side or evidence-side prompt,
  in any doctor-visible state channel, or in encounter-graph checkpoints, both
  at start and after a full scripted encounter.
- Visibility tests for each agent's context builder.
- Gatekeeper matching tests: synonyms, abbreviations, unlisted tests.
- Graph tests: routing for every action, invalid output handling, turn cap,
  spend cap, recursion limit, forced finalize.
- Download tests: checksum mismatch, line-count mismatch, network failure
  (mocked).
- Tests calling real APIs are marked `@pytest.mark.live` and skipped by
  default.

---

## 12. Layout and code quality

**[ASK]** to approve or modify this proposed layout before creating it:

```
agentclinic/
  pyproject.toml
  README.md
  .env.example
  config/            models.yaml, budgets.yaml, test_costs.yaml, prompts/
  dataset/           downloaded JSONL files, MANIFEST.json, splits.json
  docs/              PLAN.md, PLAN_REVIEW.md, DECISIONS.md, PHASE_N_NOTES.md
  scripts/           download_dataset.py
  src/agentclinic/
    data/            loader.py, models.py, views.py
    llm/             openrouter.py, fake.py, costs.py
    graphs/          encounter.py, single_doctor.py, doctor_panel.py,
                     judge.py, state.py, routing.py
    agents/          patient.py, gatekeeper.py, doctor/, evidence.py, judge.py
    tools/           pubmed.py, openfda.py
    eval/            runner.py, metrics.py, report.py, calibrate.py
    experiments/     leakage.py, counterfactual.py, abstention.py
    tracing.py
    cli.py
  runs/              outputs
  tests/
```

Code quality:
- Type hints everywhere; docstrings explain *why*.
- Prompts in `config/prompts/`, not in code.
- Small, readable modules; clarity over cleverness.
- Each graph module includes a short comment block describing its nodes and
  edges, and the plan's Mermaid diagram is kept in sync with the code.

Begin with Section 1, Step 1: ask me how I want the dataset downloaded.
