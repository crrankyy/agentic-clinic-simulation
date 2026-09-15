"""Loader behaviour, including every irregular shape inspection turned up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentclinic.data.loader import DatasetLoadError, load_cases

MINIMAL = {
    "OSCE_Examination": {
        "Objective_for_Doctor": "Assess the patient.",
        "Patient_Actor": {
            "Demographics": "40-year-old man",
            "History": "Two days of cough.",
            "Symptoms": {"Primary_Symptom": "cough", "Secondary_Symptoms": ["fever"]},
        },
        "Physical_Examination_Findings": {"Vital_Signs": {"HR": "88"}},
        "Test_Results": {"Complete_Blood_Count": {"WBC": "normal"}},
        "Correct_Diagnosis": "Common cold",
    }
}


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return path


def test_loads_the_real_dataset(cases):
    assert len(cases) == 214


def test_case_ids_are_zero_padded_line_numbers(cases):
    assert cases[0].case_id == "medqa-0001"
    assert cases[-1].case_id == "medqa-0214"
    assert cases[12].line_number == 13
    assert len({c.case_id for c in cases}) == 214


def test_blank_lines_are_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps(MINIMAL) + "\n\n   \n" + json.dumps(MINIMAL), encoding="utf-8")
    assert len(load_cases(p, expected=2)) == 2


def test_malformed_json_fails_loudly_with_the_line_number(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps(MINIMAL) + "\n{ not json\n", encoding="utf-8")
    with pytest.raises(DatasetLoadError, match=r":2 is not valid JSON"):
        load_cases(p, expected=2)


def test_unexpected_top_level_key_fails(tmp_path):
    p = write_jsonl(tmp_path / "x.jsonl", [{"Something_Else": {}}])
    with pytest.raises(DatasetLoadError, match="expected exactly"):
        load_cases(p, expected=1)


def test_case_count_mismatch_fails(tmp_path):
    p = write_jsonl(tmp_path / "x.jsonl", [MINIMAL])
    with pytest.raises(DatasetLoadError, match="expected 99 cases but parsed 1"):
        load_cases(p, expected=99)


# --- the irregular shapes, all of which must load (D-018) -------------------

def test_the_four_empty_test_results_cases_load(cases):
    empty = [c.line_number for c in cases if not c.has_test_results]
    assert empty == [69, 106, 111, 209]


def test_string_and_list_values_in_test_results_load(cases):
    any_str = [c.line_number for c in cases
               if any(isinstance(v, str) for v in c.test_results.values())]
    assert len(any_str) == 16, "16 cases have top-level string values, not 2"

    def has_list(o):
        if isinstance(o, dict):
            return any(has_list(v) for v in o.values())
        return isinstance(o, list)

    assert [c.line_number for c in cases if has_list(c.test_results)] == [153, 185]
    assert [c.line_number for c in cases
            if has_list(c.physical_examination_findings)] == [37, 103]


def test_patient_actor_irregularities_load(cases):
    by_line = {c.line_number: c for c in cases}
    assert by_line[120].patient_actor.past_medical_history is None   # missing entirely
    assert by_line[132].patient_actor.symptoms.primary_symptom is None  # Symptoms == {}
    # Non-string shapes in fields the brief describes as text.
    assert any(not isinstance(c.patient_actor.review_of_systems, (str, type(None)))
               for c in cases)


def test_undocumented_patient_keys_are_preserved_not_dropped(cases):
    """A patient on medications must not be able to answer 'none' (brief §5.1)."""
    with_extras = {c.line_number: sorted(c.patient_actor.extra_fields)
                   for c in cases if c.patient_actor.extra_fields}
    assert sorted(with_extras) == [18, 21, 26, 43, 77, 98, 132, 179, 191]
    assert with_extras[18] == ["Current_Medications"]
