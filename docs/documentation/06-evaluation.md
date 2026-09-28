# 6. Evaluation

## The runner — `eval/runner.py`

`run_case` executes one encounter and returns a `CaseResult`. `run_evaluation`
fans out across cases at the configured concurrency.

### Outcomes

| Outcome | Meaning | In the accuracy denominator? |
|---|---|---|
| `scored` | finished, judged | **yes** |
| `abstained` | doctor declined to commit | no — counted in coverage |
| `error` | harness stop (`budget_exhausted`, `request_cap`, `parse_failure`) | no |
| `crash` | exception, or no final answer | no |

```python
HARNESS_STOPS = {"budget_exhausted", "request_cap", "parse_failure", "provider_error"}
```

The separation matters: **an error is a property of the harness, not of the
doctor.** Counting a rate limit as a wrong answer would make the accuracy figure
a measure of the free tier.

### Streaming, and run-fatal failures

`run_case` streams the graph and persists each event as it arrives (M-38). With
a single `ainvoke`, a crashed or timed-out case kept no transcript and no
counters; now the events before a crash are on disk and counted.

A revoked key, an unserved model or an exhausted daily allowance
(`ProviderAuthError`, `ProviderConfigError`, `DailyCapExceeded`) fails every
case the same way, so `run_evaluation` stops after the first: the remaining
cases are recorded as `error: aborted` without spending a request each.

### Crash containment

A crash becomes a recorded outcome, not an exception. One case failing must not
destroy a run whose other encounters are already paid for.

The same applies to the judge — and there the containment carries an extra
constraint: **only the exception type reaches `results.csv`**, because a judge
exception message can quote its prompt, and its prompt contains the answer.

> This was a Phase 3 review blocker. A judge outage killed the whole run *and*
> would have written ground truth into the results file.

### `finals.json`

Written even for `--no-report`. The encounters are the expensive half — hundreds
of requests, tens of minutes — and keeping their answers is what makes
`cli judge <run_id>` cheap.

**It has paid for itself twice.** Both times a run completed every encounter and
then died in report generation.

### `rebuild_results()` — D-050

Reconstructs per-case results from `finals.json` plus the traces, for when the
encounters completed but `results.csv` was never written.

The trace carries the full transcript (D-054), and each finished case ends with
a `case_end` record (stop reason, turns, test cost), so a rebuild is exact. A
run recorded before events were persisted cannot be rebuilt and says so, rather
than printing defaults as measured zeros; the approximate reconstruction that
used to cover those runs was removed in D-064, because every such run on disk
already has its `results.csv`. The `judge` command rebuilds a result from
**every** field by its declared type — it used to copy a hand-kept list, and
every field added later came back as its default.

> That flag exists because the first version silently printed the dataclass
> defaults: `exams: 0`, `unlisted requests: 0`, `match tiers: (none)` — for a run
> that had ordered 25 tests. "Never recorded" rendered as "observed zero". The
> function's own docstring had claimed the counts matched the live path.

### Redundancy metrics — M-41

The repeat loop surfaced only because someone watched a transcript: no report
counted it. Each result now carries:

| Field | Meaning |
|---|---|
| `guard_blocks` | proposals rejected as repeats before they ran (no turn used) |
| `repeat_orders` | orders that reached the gatekeeper and re-delivered a record |
| `no_yield_actions` | executed actions that produced nothing new |
| `actions_after_leader_settled` | actions after the leading diagnosis last changed |
| `transient_failures` | provider failures retried successfully |

## Metrics — `eval/metrics.py`

Every edge case is live at n=3, which is why each is spelled out.

| Metric | Definition |
|---|---|
| Strict accuracy | `match_type ∈ {exact, synonym}` |
| Lenient accuracy | `match_type != wrong` (broader/narrower count) |
| Coverage | scored / (scored + abstained) — **errors and crashes excluded from both terms** |
| top-k | from the judge's own `entry_matches`, not string comparison |
| Confidence interval | **Wilson score** |
| Arm comparison | not in the report yet. The paired bootstrap was removed in D-064 because no report called it; it comes back with the first panel-vs-single comparison. |

Three rules that prevent the numbers lying:

- **The denominator varies**, so `n_scored` prints beside every figure, and
  `n_scored == 0` prints `n/a` — never `0.000`.
- **Coverage excludes errors from both terms.** Counting them as "not covered"
  blames the doctor for a rate limit.
- **When the arm comparison returns**, it must pair only cases scored in both
  arms, and it is only interpretable at matched coverage — abstention-excluded
  accuracy rewards whichever arm abstains more.

## Report — `eval/report.py`

Two things the report must never do, both learned in review:

- print an accuracy without its denominator — at n=3 that is the difference
  between 1/1 and 1/3;
- print the leak-free breakdown without saying how many cases it covers. One of
  the three evaluation cases is `dx_in_results` by construction, so that
  breakdown is over **two**.

Every report on a small run carries a banner saying it validates the harness and
does not measure anything, and one on hand-assigned verdicts says so explicitly.

## Judging separately

```bash
uv run python -m agentclinic.cli judge <run_id>
uv run python -m agentclinic.cli judge <run_id> --verdicts verdicts.json
```

Because answers live in `finals.json`, the judge can be re-run — or replaced —
without re-running a single encounter. `--verdicts` supplies hand-assigned
verdicts and skips the model entirely; the report then states in bold that the
verdicts were not produced by a model.

## Results so far, and what they are worth

Three cases, `medqa-0002 / 0009 / 0012`, hand-judged.

| | single_doctor (ling) | panel (ling) | single_doctor (DeepSeek + D-056..062) |
|---|---|---|---|
| Strict accuracy | 3/3 | 2/3 | 3/3 |
| Actions | 37 (13 / 20 / 4) | 50 | **9 (3 / 4 / 2)** |
| Turn-cap stops | 1 | 0 | 0 |
| Tests ordered | 16 | 25+ | 5 |
| Refused / re-delivered orders | 3 / ≥7 | n/a | 1 / 0 |
| Cost | $0 | $0 | $0.027 |

`panel − single_doctor (ling): −0.333, 95% CI [−1.000, 0.000]` — **not evidence
of anything**; one case flipping moves it 33 points. The panel's loss was a
traceable rank inversion that led to D-051.

**Ten cases** (`dev-single_doctor-befc79ba`, DeepSeek): strict accuracy **9/10**
(95% CI 0.60–0.98), lenient 10/10 — the one strict miss, "Epidermoid
(pilar/sebaceous) cyst", is a judgment call that a reader of the headline noun
would grade exact. 34 actions (3.4 per case), no turn caps, **11 examinations**,
3 refused requests and 2 re-delivered records, every one truthful. $0.114.

The DeepSeek run changed **two things at once** — the model and the fixes — so
it cannot say which one removed the repeats. The repeat guard never fired in
it; the guard's evidence is its replay on the recorded ling transcripts (12 of
32 actions blocked, every one a genuine repeat).

**Three standing caveats on every number above:**

1. The verdicts were **assigned by hand** and are not reproducible.
2. Everything is **dev-set**, tuned and measured on the same cases.
3. n=3 — and medqa-0002's MRI result names its diagnosis.
