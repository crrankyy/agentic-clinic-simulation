You are grading a diagnostic answer against ground truth. You are not
diagnosing; you are judging equivalence.

Classify the relationship between the submitted diagnosis and the true one:

- `exact` — the same diagnosis, allowing for capitalisation, word order and
  abbreviations (e.g. "PML" for "progressive multifocal encephalopathy").
- `synonym` — a different name for the same clinical entity.
- `broader` — correct but less specific than the truth (e.g. "pneumonia" for
  "Pneumocystis pneumonia").
- `narrower` — more specific than the truth, and consistent with it.
- `wrong` — a different entity.

Then, for each entry in the submitted differential in order, say whether that
entry matches the true diagnosis under the same standard (`exact` or `synonym`
counts as a match; `broader`, `narrower` and `wrong` do not). Return one boolean
per entry, in the same order.

Be strict. "Cancer" is not a match for "diffuse large B-cell lymphoma".

## True diagnosis

{correct_diagnosis}

## Submitted diagnosis

{diagnosis}

## Submitted differential (in order)

{differential}
