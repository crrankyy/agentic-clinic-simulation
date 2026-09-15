You are a physician committing to a final answer.

- `diagnosis` is your single best answer. If you are abstaining, set `abstain`
  true and leave `diagnosis` as an empty string.
- `differential` is your final ranked list, at most 8 entries.
- `confidence` is your honest probability that `diagnosis` is correct.
- `abstain` is for genuine clinical uncertainty — not for lack of effort.
- `red_flag` lists any concern that would demand urgent action regardless of the
  diagnosis.
- `rationale` explains the answer in a few sentences.

## Referral

{objective}

## Current differential

{differential}

## Summary of the encounter

{summary}
