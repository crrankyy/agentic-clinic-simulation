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
HARNESS_STOPS = {"budget_exhausted", "request_cap", "parse_failure"}
```

The separation matters: **an error is a property of the harness, not of the
doctor.** Counting a rate limit as a wrong answer would make the accuracy figure
a measure of the free tier.

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

Runs recorded since **D-054** carry their full transcript, so counters come back
exact. For older runs the counters are derived from per-node LLM calls and are
**strictly weaker**:

- `patient_questions` is exact (`ask_patient` always calls the model)
- `turns` and `tests_ordered` are **lower bounds** — the gatekeeper only calls
  the model when its cheap tiers miss, so a cheaply-resolved test leaves no record
- exams, unlisted requests, match tiers, red flags, simulated cost are **not
  recoverable at all**

Such rows carry `behaviour_recovered=False` and the report prints
`n/a (not recorded)`.

> That flag exists because the first version silently printed the dataclass
> defaults: `exams: 0`, `unlisted requests: 0`, `match tiers: (none)` — for a run
> that had ordered 25 tests. "Never recorded" rendered as "observed zero". The
> function's own docstring had claimed the counts matched the live path.

## Metrics — `eval/metrics.py`

Every edge case is live at n=3, which is why each is spelled out.

| Metric | Definition |
|---|---|
| Strict accuracy | `match_type ∈ {exact, synonym}` |
| Lenient accuracy | `match_type != wrong` (broader/narrower count) |
| Coverage | scored / (scored + abstained) — **errors and crashes excluded from both terms** |
| top-k | from the judge's own `entry_matches`, not string comparison |
| Confidence interval | **Wilson score** |
| Arm comparison | **paired bootstrap**, restricted to cases scored in *both* arms |

Three rules that prevent the numbers lying:

- **The denominator varies**, so `n_scored` prints beside every figure, and
  `n_scored == 0` prints `n/a` — never `0.000`.
- **Coverage excludes errors from both terms.** Counting them as "not covered"
  blames the doctor for a rate limit.
- **The paired bootstrap needs actual pairs.** A case the panel abstained on and
  the single doctor answered is not one.
- **Panel-minus-single accuracy is only interpretable at matched coverage** —
  abstention-excluded accuracy rewards whichever arm abstains more, so the report
  warns when coverages differ.

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

| | single_doctor | panel |
|---|---|---|
| Strict accuracy | 1.000 (3/3), CI [0.44, 1.00] | 0.667 (2/3), CI [0.21, 0.94] |
| top-1 / top-3 | 3/3 · 3/3 | 2/3 · 3/3 |
| Mean confidence | 0.84 | 0.43 |
| Turns | 37 | 50 |

`panel − single_doctor: −0.333, 95% CI [−1.000, 0.000]`.

**This is not evidence of anything.** One case flipping moves the point estimate
by 33 points, and the interval spans nearly the whole range.

What *is* worth recording is the mechanism, because it is traceable. Both arms
score **top-3 = 3/3** — the panel did not lose `medqa-0012`, it **demoted** it.
That diagnosis of the failure led to `D-051`.

**Three standing caveats on every number above:**

1. The verdicts were **assigned by hand** and are not reproducible.
2. Everything is **dev-set**, tuned and measured on the same cases.
3. n=3.
