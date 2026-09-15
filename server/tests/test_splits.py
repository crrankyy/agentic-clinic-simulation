"""Splits: deterministic, grouped, written once."""

from __future__ import annotations

import pytest

from agentclinic.data.splits import (
    SplitError,
    build_splits,
    group_key,
    select_eval_subset,
    serialise,
    write_splits,
)


def test_sizes_are_exactly_40_and_174(cases):
    s = build_splits(cases)
    assert len(s["dev"]) == 40
    assert len(s["heldout"]) == 174
    assert not set(s["dev"]) & set(s["heldout"])


def test_no_diagnosis_spans_both_splits(cases):
    """Grouped, not stratified — the whole point of the split design."""
    s = build_splits(cases)
    by_id = {c.case_id: c for c in cases}
    dev_keys = {group_key(by_id[c].correct_diagnosis) for c in s["dev"]}
    held_keys = {group_key(by_id[c].correct_diagnosis) for c in s["heldout"]}
    assert not dev_keys & held_keys


def test_grouping_is_case_insensitive(cases):
    """35 groups over 72 cases normalised, vs 31 over 63 raw."""
    raw = {}
    norm = {}
    for c in cases:
        raw.setdefault(c.correct_diagnosis, []).append(c.case_id)
        norm.setdefault(group_key(c.correct_diagnosis), []).append(c.case_id)
    assert sum(1 for v in raw.values() if len(v) > 1) == 31
    assert sum(1 for v in norm.values() if len(v) > 1) == 35


def test_regeneration_is_byte_for_byte_identical(cases):
    assert serialise(build_splits(cases)) == serialise(build_splits(cases))


def test_split_is_independent_of_input_order(cases):
    reversed_cases = list(reversed(cases))
    assert serialise(build_splits(cases)) == serialise(build_splits(reversed_cases))


def test_a_different_seed_gives_a_different_split(cases):
    assert build_splits(cases)["dev"] != build_splits(cases, seed=1)["dev"]


def test_write_refuses_to_overwrite(tmp_path, cases):
    path = tmp_path / "splits.json"
    write_splits(build_splits(cases), path)
    with pytest.raises(SplitError, match="written once"):
        write_splits(build_splits(cases), path)


def test_eval_subset_is_the_expected_three_cases(cases):
    s = build_splits(cases)
    assert select_eval_subset(cases, s["dev"]) == ["medqa-0002", "medqa-0009", "medqa-0012"]


def test_eval_subset_always_includes_a_flagged_case(cases):
    s = build_splits(cases)
    by_id = {c.case_id: c for c in cases}
    chosen = select_eval_subset(cases, s["dev"])
    assert any(by_id[c].dx_in_results for c in chosen)


def test_dev_contains_no_empty_test_results_case(cases):
    """Documents why D-029 dropped that requirement — it is unsatisfiable here."""
    s = build_splits(cases)
    by_id = {c.case_id: c for c in cases}
    assert not [c for c in s["dev"] if not by_id[c].has_test_results]
