# 9. Operations

## Setup

```bash
uv sync                                                    # Python 3.12, pinned
uv run python server/scripts/download_dataset.py           # fetch + verify cases
cp .env.example .env                                       # then fill in
uv run pytest                                              # 266 tests, offline
```

Python is pinned to **3.12**: the system 3.14 is ahead of much of the
LangChain/LangGraph wheel ecosystem (`D-007`). `uv` manages its own interpreter
and the system Python is untouched.

## Secrets

**Environment variables only. Never logged.**

| Variable | Needed for |
|---|---|
| `OPENROUTER_API_KEY` | every agent role |
| `ANTHROPIC_API_KEY` | the judge, *if* using API billing |
| `NCBI_EMAIL`, `NCBI_API_KEY` | Phase 5 evidence agent — **not yet built** |

`.env` is git-ignored; `.env.example` is committed with placeholders. Real
environment variables always take precedence over a stale `.env`. `load_dotenv`
returns the **names** it loaded, never the values, so startup output is safe to
paste.

## Commands

```bash
uv run python -m agentclinic.cli dataset          # case summary + leakage flags
uv run python -m agentclinic.cli splits --write   # build the committed split
uv run python -m agentclinic.cli config           # resolved configuration
uv run python -m agentclinic.cli play             # drive the doctor yourself
uv run python -m agentclinic.cli run              # evaluate (single_doctor)
uv run python -m agentclinic.cli run --config panel
uv run python -m agentclinic.cli judge <run_id>   # re-judge without re-running
uv run python -m agentclinic.cli probe --providers "Together,DeepInfra"   # test a model on the real schemas
uv run python -m agentclinic.cli trace <run_id> <case_id>
uv run python -m agentclinic.cli serve            # the web viewer
```

### Flags worth knowing

| Flag | Effect |
|---|---|
| `--limit N` | first N cases |
| `--max-turns N` | shorter encounters; the main cost lever |
| `--no-report` | traces only |
| `--cache` | replay cached completions — **requires `--no-report`** |
| `--split heldout` | the sealed split. Prints a warning |
| `--verdicts <path>` | hand-assigned verdicts, no judge model |

`--cache` refusing to run without `--no-report` is an **assertion, not a flag
default**, so a reported run cannot replay cached completions by accident
(`Q-34`).

## Configuration

Model IDs live in `server/config/models.yaml`, **never in code**.

```yaml
provider:
  pin: ["Together"]                 # chosen by `cli probe` (D-061)
  allow_fallbacks: false
structured_output:
  method: function_calling
model: deepseek/deepseek-v4.1-flash   # every role uses this one model
role_settings:                      # per-role output and thinking limits
  default:    {max_tokens: 4000, reasoning: {effort: medium, exclude: true}}
  hypothesis: {max_tokens: 8000, reasoning: {effort: medium, exclude: true}}
  patient:    {max_tokens: 1500, reasoning: {effort: low, exclude: true}}
  gatekeeper: {max_tokens: 800,  reasoning: {effort: low, exclude: true}}
judge:
  model: claude-opus-5              # Anthropic SDK, not OpenRouter (D-021)
  auth: subscription
```

**One model for every role** (`D-020`'s surviving half; its `:free` half was
superseded by D-061 at the user's direction). This removes any role/model
confound from the panel-vs-single comparison.

**Changing the model or provider?** Run `cli probe` first. It sends the real
nested schemas to each provider you name and reports pass/fail and latency.
A flat-schema check once passed a model that then scored 0/2 on the real one,
and the endpoint listing cannot see your account's guardrails — first-party
DeepSeek looked available and was refused on every call.

`judge.auth: subscription` **refuses to run** if `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN` is set, because the SDK would silently prefer either over
an `ant auth login` profile and bill per token (`D-046`). Set `auth: any` to
allow API-key billing deliberately.

> A correction worth recording: `ant auth login` OAuths against
> `platform.claude.com` with workspace binding — that is **API Console billing**,
> not a Claude.ai subscription. There is no subscription path for SDK calls.

### Budgets — `server/config/budgets.yaml`

```yaml
encounter:
  max_turns: 20
  recursion_limit_multiplier: {single_doctor: 6, panel: 10}
spend:
  per_case_usd: 0.50
  per_run_usd: 25.0
requests:
  rate_per_minute: 18        # free tier only (D-062)
  per_day: 1000              # free tier only
  paid_rate_per_minute: 60
  concurrency: 4
retries:
  content_attempts: 3        # repair prompts, for output that did not validate
  transient_attempts: 5      # 429 / 5xx / connection / empty: back off, resend unchanged
  timeout_retries: 2
  backoff_base_s: 2          # 2, 4, 8, 16, 32 s with ±20% jitter; Retry-After wins
  backoff_cap_s: 60
  timeout_seconds: 120
guard:
  question_repeat_overlap: 0.6667
```

Never retried: 401/402/403 and 400/404/422. Both are run-fatal.

### Test prices — `server/config/test_costs.yaml`

**Illustrative, not a fee schedule** (`Q-13`). They exist so the cost steward has
something to reason about and so relative comparisons between runs mean
something. **Absolute figures mean nothing and must never be quoted as real
healthcare costs.**

Unknown tests are priced at the table's **median**: zero would make unmatched
tests free and invite the doctor to order them; a penalty would punish the doctor
for the dataset's key naming.

## What a run costs

**DeepSeek v4.1 Flash on Together** (the current configuration), measured on
the 2026-09-27 sim: **$0.027 for three single-doctor cases**, 2–4 actions each,
24–33 s of model time per case, no provider failures. The key's remaining
spend limit is printed before a run and shown in the viewer.

**The free model** (earlier runs), measured:

| | calls/case | time/case | dev (40) | full (214) |
|---|---|---|---|---|
| `single_doctor` | ~41 | ~4 min | 1,630 | 8,800 |
| `panel` | ~77 | ~9 min | 3,090 | 16,500 |

Against the **1000 requests/day** free-tier cap, both arms on the dev split was
about five days. That cap does not apply to a paid model.

`cli run` refuses to start if the preflight fails or, on the free tier, if the
projected worst case exceeds the remaining allowance; so does `POST /api/runs`.

## Run artefacts

```
runs/<run_id>/
├── traces/<case_id>.jsonl   # every LLM call and every event
├── judge.jsonl              # judge records — never in a per-case trace
├── finals.json              # the answers: makes re-judging cheap
├── summary.json             # status, stop_reason, error_class (web runs, every exit)
├── results.csv              # per-case results
└── report.md                # the rendered report
```

`runs/` is git-ignored.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every case is `error` with `request_cap` | Daily allowance gone. Resets at **00:00 UTC**, not local midnight |
| A run hangs for many minutes | httpx timeouts are **per-operation**; `asyncio.wait_for` bounds it, but check `runs/<id>/traces/` for the last record |
| `results.csv` missing after a crash | `cli judge <run_id>` rebuilds from `finals.json` + traces (`D-050`) |
| Report says `n/a (not recorded)` | A rebuilt run recorded before **D-054**; its transcript was never persisted |
| Tests skip en masse | Dataset not downloaded — `test_paths.py` says which anchor failed |
| Judge refuses to start | `auth: subscription` with an API key set (`D-046`) |
| Web run 429s on start | Pre-flight projection exceeds the remaining allowance (free tier) |
| Web run 503s on start / Start disabled | The preflight failed; the page shows why (revoked key, exhausted spend limit, provider refused) |
| `ProviderAuthError` | 401/402/403 — the key is revoked, or its spend limit is exhausted. The run aborts |
| `ProviderConfigError` / "No endpoints found" | 400/404 — model, provider or parameter not served, or refused by an account guardrail. Run `cli probe` |
| Cases end with `provider_error` | The provider kept failing after every retry; outcome `error`, excluded from accuracy |
| A case ends with `no_new_actions` | Every proposal in one decision repeated an earlier action; a clinical stop, scored |
| `usage.cost` is always 0 | Expected on free models; real on paid ones |

## Project status

| Phase | State |
|---|---|
| 0–4 | Complete |
| **5** | Evidence agent + judge calibration — **not started**; needs `NCBI_EMAIL` |
| **6** | Experiments: leakage probe, counterfactuals — needs sign-off on the sets |
| 7 | NEJM multimodal — explicitly not planned |

**Three standing caveats on every result produced so far:**

1. Verdicts were **assigned by hand** (`D-047`) — a one-off reading, not
   reproducible, and not satisfying the calibration requirement.
2. Everything is **dev-set**, tuned and measured on the same cases.
3. **n=3.**

Phase 5 fixes (1) by construction. Nothing downstream is trustworthy until it
does.
