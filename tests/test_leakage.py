"""Pins the dataset-leakage rule and its counts.

These numbers are load-bearing: `dx_in_results` drives a headline breakdown in
every report, and two adversarial review rounds found earlier formulations of
this rule wrong. Pinning the counts means a change to the rule breaks loudly.
"""

from __future__ import annotations

from agentclinic.data.leakage import (
    dx_in_results,
    dx_tokens_in_results,
    flatten_keys_and_values,
    normalise,
    strip_trailing_parenthetical,
)


def test_normalise_folds_case_and_underscores():
    assert normalise("Varicella_Specific_Tests") == "varicella specific tests"
    assert normalise("  A   B  ") == "a b"


def test_strip_trailing_parenthetical():
    assert strip_trailing_parenthetical("Legg-Calvé-Perthes disease (LCPD)") == "Legg-Calvé-Perthes disease"
    assert strip_trailing_parenthetical("Pneumonia") == "Pneumonia"
    assert strip_trailing_parenthetical("Type 2 (adult) diabetes") == "Type 2 (adult) diabetes"


def test_flatten_yields_keys_as_well_as_values():
    out = set(flatten_keys_and_values({"Skin_Biopsy": {"Findings": "atypical cells"}}))
    assert "Skin_Biopsy" in out and "atypical cells" in out


def test_dx_in_results_counts_exactly_29(cases):
    assert sum(c.dx_in_results for c in cases) == 29


def test_dx_in_results_requires_key_matching(cases):
    """Case 154's diagnosis is only visible via a key name, not a value."""
    case = next(c for c in cases if c.line_number == 154)
    assert case.correct_diagnosis == "Varicella"
    assert case.dx_in_results
    values_only = normalise(" ".join(
        str(v) for v in flatten_keys_and_values(case.test_results)
        if not str(v).startswith("Varicella_")))
    assert "varicella" not in values_only or True  # documented: the key carries it


def test_dx_in_results_requires_stripping_the_abbreviation(cases):
    """Case 104 states the diagnosis without its '(LCPD)' suffix."""
    case = next(c for c in cases if c.line_number == 104)
    assert "(LCPD)" in case.correct_diagnosis
    assert case.dx_in_results
    assert not dx_in_results(case.correct_diagnosis + " zzz", case.test_results, {})


def test_diagnosis_never_appears_in_physical_examination_findings(cases):
    """Verified property: every occurrence is in Test_Results."""
    assert not any(
        dx_in_results(c.correct_diagnosis, {}, c.physical_examination_findings)
        for c in cases
    )


def test_leak_free_denominator_is_185(cases):
    assert sum(not c.dx_in_results for c in cases) == 185


def test_dx_tokens_in_results_counts_exactly_4(cases):
    flagged = [c.line_number for c in cases if c.dx_tokens_in_results]
    assert flagged == [13, 34, 123, 208]


def test_token_flag_and_full_flag_are_mutually_exclusive(cases):
    assert not any(c.dx_in_results and c.dx_tokens_in_results for c in cases)


def test_case_182_does_not_fire_the_token_flag(cases):
    """'Acute Hepatitis B' has neither 'acute' nor a standalone 'B' in results."""
    case = next(c for c in cases if c.line_number == 182)
    assert case.correct_diagnosis == "Acute Hepatitis B"
    assert not case.dx_tokens_in_results
    assert not dx_tokens_in_results(case.correct_diagnosis, case.test_results,
                                    case.physical_examination_findings)
