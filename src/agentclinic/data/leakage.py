"""Detection of ground-truth leakage that lives inside the dataset itself.

Why this exists: in a meaningful fraction of cases the exact `Correct_Diagnosis`
string is embedded in `Test_Results` — the data the gatekeeper is *designed* to
hand to the doctors. No state design can prevent that (see PLAN.md §3.2), so it
is measured instead of hidden, and every report gives accuracy over all cases
and over the leak-free subset.

The counts are **computed**, never hard-coded, and pinned by tests: 29 cases for
`dx_in_results` and 4 for `dx_tokens_in_results`. Both rules were arrived at the
hard way — two adversarial review rounds found earlier formulations wrong (see
docs/PLAN_REVIEW.md #8 and docs/PLAN_REVIEW_2.md B-12).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

#: Matches a trailing parenthetical abbreviation, e.g. the "(LCPD)" in
#: "Legg-Calvé-Perthes disease (LCPD)". Case 104 states the diagnosis in its
#: test results *without* the suffix, so an exact match would miss it.
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

_TOKEN = re.compile(r"[a-z0-9]+")


def strip_trailing_parenthetical(text: str) -> str:
    """Drop a trailing "(...)" abbreviation from a diagnosis string."""
    return _TRAILING_PAREN.sub("", text).strip()


def normalise(text: str) -> str:
    """Casefold, turn underscores into spaces, and collapse whitespace.

    Underscores matter because dictionary *keys* are matched too, and the
    dataset writes them as `Varicella_Specific_Tests`.
    """
    return " ".join(str(text).replace("_", " ").casefold().split())


def flatten_keys_and_values(*objects: Any) -> Iterator[str]:
    """Yield every dictionary key and every scalar value, at any depth.

    Keys are included because the gatekeeper returns a matched key *together
    with* its sub-tree: case 154's diagnosis is "Varicella" and its test results
    are filed under the key `Varicella_Specific_Tests`, so a values-only scan
    would score that case as leak-free while the doctor plainly sees the answer.
    """
    for obj in objects:
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield str(key)
                yield from flatten_keys_and_values(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from flatten_keys_and_values(value)
        elif obj is not None:
            yield str(obj)


def _results_blob(test_results: Any, exam_findings: Any) -> str:
    return normalise(" ".join(flatten_keys_and_values(test_results, exam_findings)))


def dx_in_results(diagnosis: str, test_results: Any, exam_findings: Any) -> bool:
    """True when the full diagnosis string appears in gatekeeper-visible data.

    Fires on 29 of the 214 MedQA cases. Verified never to fire on
    `Physical_Examination_Findings` alone — every occurrence is in
    `Test_Results` — but both are scanned because the gatekeeper serves both.
    """
    needle = normalise(strip_trailing_parenthetical(diagnosis))
    return bool(needle) and needle in _results_blob(test_results, exam_findings)


def dx_tokens_in_results(diagnosis: str, test_results: Any, exam_findings: Any) -> bool:
    """True when every diagnosis token appears, but the full string does not.

    A weaker reveal than `dx_in_results`: the doctor can reassemble the answer
    from the parts. Deliberately unforgiving — **all** tokens, matched at word
    boundaries, with no stopword list and no minimum length. Relaxing any of
    those produces false positives (dropping "syndrome" makes case 31 fire
    against its own objective) and dropping short tokens produces false
    negatives that are not really complete reveals (case 182, "Acute Hepatitis
    B", has neither "acute" nor a standalone "B" anywhere in its results).

    Fires on exactly 4 cases: 13, 34, 123, 208.
    """
    if dx_in_results(diagnosis, test_results, exam_findings):
        return False  # the stronger flag already covers it
    needle = normalise(strip_trailing_parenthetical(diagnosis))
    tokens = _TOKEN.findall(needle)
    if not tokens:
        return False
    blob = _results_blob(test_results, exam_findings)
    return all(re.search(rf"\b{re.escape(t)}\b", blob) for t in tokens)
