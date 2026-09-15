"""Gatekeeper matching against the real key inventory."""

from __future__ import annotations

import pytest

from agentclinic.agents.gatekeeper import UNAVAILABLE, Gatekeeper, normalise
from agentclinic.config import load_test_costs
from agentclinic.data.views import CaseStore


@pytest.fixture(scope="session")
def costs():
    return load_test_costs()


@pytest.fixture(scope="session")
def store(cases):
    return CaseStore(cases)


@pytest.fixture(scope="session")
def by_line(cases):
    return {c.line_number: c for c in cases}


def gk(store, costs, case_id, llm=None):
    return Gatekeeper(store.gatekeeper_view(case_id), costs, llm_disambiguate=llm)


def test_normalise_handles_case_underscores_and_hyphens():
    assert normalise("Chest_X-ray") == normalise("Chest_X-Ray") == "chest x ray"


async def test_exact_match(store, costs, by_line):
    g = gk(store, costs, by_line[10].case_id)
    r = await g.respond("Complete_Blood_Count")
    assert (r.tier, r.key, r.unlisted) == ("exact", "Complete_Blood_Count", False)


@pytest.mark.parametrize("request_text", ["CBC", "cbc", "full blood count", "haemogram"])
async def test_synonyms_and_abbreviations(store, costs, by_line, request_text):
    r = await gk(store, costs, by_line[10].case_id).respond(request_text)
    assert r.key == "Complete_Blood_Count" and r.tier == "synonym"


async def test_case_variant_keys_match_without_a_synonym_entry(store, costs, cases):
    """`Chest_X-ray` and `Chest_X-Ray` both exist in the dataset."""
    for variant in ("Chest_X-ray", "Chest_X-Ray"):
        case = next((c for c in cases if variant in c.test_results), None)
        if case is None:
            continue
        r = await gk(store, costs, case.case_id).respond("chest x-ray")
        assert r.tier in {"exact", "synonym"} and r.key == variant


async def test_leaf_request_matches_its_parent(store, costs, by_line):
    """Ordering an analyte returns the panel it belongs to."""
    case = by_line[10]
    leaf = next(iter(case.test_results["Complete_Blood_Count"]))
    g = gk(store, costs, case.case_id)
    # Urinalysis also has this leaf in case 10, so use a CBC-only analyte.
    cbc_only = set(case.test_results["Complete_Blood_Count"]) - set(case.test_results["Urinalysis"])
    r = await g.respond(next(iter(cbc_only)))
    assert r.key == "Complete_Blood_Count" and r.tier == "leaf"


async def test_ambiguous_leaf_is_not_answered_without_disambiguation(store, costs, by_line):
    """WBC sits under both CBC and Urinalysis in case 10 (D-035)."""
    m = await gk(store, costs, by_line[10].case_id).match("WBC")
    assert m.tier == "unmatched"
    assert set(m.candidates) == {"Complete_Blood_Count", "Urinalysis"}


async def test_ambiguous_leaf_returns_exactly_one_panel_when_disambiguated(store, costs, by_line):
    async def choose(request, candidates):
        return "Complete_Blood_Count"

    m = await gk(store, costs, by_line[10].case_id, llm=choose).match("WBC")
    assert m.tier == "llm_disambiguated" and m.key == "Complete_Blood_Count"
    # Never both: returning the urinalysis would disclose a test never ordered.
    assert "Urinalysis" not in str(m.payload)


async def test_unlisted_test_is_unavailable_but_still_charged(store, costs, by_line):
    """Q-12: free unavailability would let the doctor probe the key space."""
    r = await gk(store, costs, by_line[10].case_id).respond("positron emission tomography")
    assert r.text == UNAVAILABLE and r.unlisted and r.cost_usd == costs.unknown_price


@pytest.mark.parametrize("line", [69, 106, 111, 209])
async def test_the_four_empty_test_results_cases(store, costs, by_line, line):
    """No test can ever match; the path must still be well-defined."""
    r = await gk(store, costs, by_line[line].case_id).respond("CBC")
    assert r.text == UNAVAILABLE and r.unlisted


@pytest.mark.parametrize("line", [150, 187])
async def test_string_valued_test_results_render(store, costs, by_line, line):
    case = by_line[line]
    key = next(iter(case.test_results))
    r = await gk(store, costs, case.case_id).respond(key)
    assert r.key == key and r.text and "None" not in r.text


@pytest.mark.parametrize("line", [153, 185])
async def test_list_valued_test_results_render(store, costs, by_line, line):
    case = by_line[line]
    for key in case.test_results:
        r = await gk(store, costs, case.case_id).respond(key)
        assert r.key == key and r.text


@pytest.mark.parametrize("line", [37, 103])
async def test_list_valued_exam_findings_render(store, costs, by_line, line):
    """`request_exam` walks PEF, which contains lists in exactly these cases."""
    case = by_line[line]
    for key in case.physical_examination_findings:
        r = await gk(store, costs, case.case_id).respond(key, "exams")
        assert r.key == key and r.text


async def test_exam_domain_is_separate_from_tests(store, costs, by_line):
    """An exam request must never reach Test_Results, or vice versa."""
    case = by_line[10]
    r = await gk(store, costs, case.case_id).respond("Complete_Blood_Count", "exams")
    assert r.unlisted, "a test name must not match in the exam domain"


async def test_gatekeeper_cannot_see_anything_but_its_view(store, costs, by_line):
    g = gk(store, costs, by_line[2].case_id)
    blob = repr(g.view).casefold()
    assert by_line[2].correct_diagnosis.casefold() not in blob or by_line[2].dx_in_results
