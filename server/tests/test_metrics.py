"""Scoring arithmetic, concentrating on the edges that are live at n=3."""

from __future__ import annotations

from agentclinic.eval.metrics import calibration_bins, summarise, wilson


def test_abstentions_and_errors_leave_the_denominator():
    s = summarise(["scored", "abstained", "error", "crash", "scored"],
                  [True, False, False, False, False])
    assert (s.n_scored, s.n_abstained, s.n_error, s.n_crash) == (2, 1, 1, 1)
    assert s.accuracy == 0.5


def test_zero_scored_prints_na_not_zero():
    """0.0 would read as 'the doctor got everything wrong'."""
    s = summarise(["abstained", "error"], [False, False])
    assert s.accuracy is None
    assert s.render() == "n/a (n_scored=0)"


def test_single_scored_case_is_flagged():
    s = summarise(["scored"], [True])
    assert s.render().endswith("(n=1)")


def test_coverage_excludes_errors_from_both_terms():
    """An API failure must not be counted as the doctor declining to answer."""
    s = summarise(["scored", "abstained", "error"], [True, False, False])
    assert s.coverage == 1 / 2


def test_coverage_is_na_when_nothing_was_scored_or_abstained():
    assert summarise(["error", "crash"], [False, False]).coverage is None


def test_wilson_is_defined_at_the_extremes():
    assert wilson(0, 0) is None
    lo_hi = wilson(3, 3)
    assert lo_hi.low > 0.29 and lo_hi.high == 1.0
    assert wilson(0, 3).low == 0.0


def test_wilson_narrows_as_n_grows():
    small, large = wilson(5, 10), wilson(50, 100)
    assert (large.high - large.low) < (small.high - small.low)


def test_calibration_returns_empty_bins_rather_than_hiding_them():
    bins = calibration_bins([0.95], [True])
    assert len(bins) == 10
    assert sum(b.count for b in bins) == 1
    assert bins[0].count == 0 and "n=0" in bins[0].render()


def test_confidence_of_exactly_one_lands_in_the_top_bin():
    bins = calibration_bins([1.0], [True])
    assert bins[-1].count == 1
