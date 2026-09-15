"""Load MedQA OSCE cases from the downloaded JSONL files.

Behaviour decided in `docs/DECISIONS.md`:

* **Q-01** — case IDs are `medqa-0001` … `medqa-0214`, the 1-based line number
  zero-padded. Stable because the download is pinned to a commit (D-003), and
  readable in trace filenames and logs, which matters more than content
  addressing for a project whose main activity is reading traces.
* **Q-02** — a malformed record **fails the whole load**, naming the line. The
  file currently has zero malformed lines; resilience here would only buy the
  ability to evaluate silently on a partial dataset.
* **D-018** — irregular shapes are modelled, not rejected. All 214 cases load.
"""

from __future__ import annotations

import json
from pathlib import Path

from .leakage import dx_in_results, dx_tokens_in_results
from .models import Case

MEDQA_EXTENDED = "agentclinic_medqa_extended.jsonl"
EXPECTED_CASES = {MEDQA_EXTENDED: 214, "agentclinic_medqa.jsonl": 107}


class DatasetLoadError(RuntimeError):
    """A record could not be parsed, or the file disagrees with the brief."""


def _case_id(line_number: int) -> str:
    return f"medqa-{line_number:04d}"


def load_cases(path: Path, *, expected: int | None = None) -> list[Case]:
    """Parse one JSONL file into `Case` objects.

    `expected` defaults to the count the brief records for this filename; pass
    an explicit value in tests using fixture files.
    """
    if expected is None:
        expected = EXPECTED_CASES.get(path.name)

    cases: list[Case] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue  # blank lines are not cases; the last line may lack a newline
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DatasetLoadError(
                f"{path}:{line_number} is not valid JSON ({exc}). "
                "Refusing to load a partial dataset."
            ) from exc

        if set(record) != {"OSCE_Examination"}:
            raise DatasetLoadError(
                f"{path}:{line_number} has top-level keys {sorted(record)}; "
                "expected exactly ['OSCE_Examination']."
            )

        osce = record["OSCE_Examination"]
        try:
            case = Case(
                case_id=_case_id(line_number),
                line_number=line_number,
                objective_for_doctor=osce["Objective_for_Doctor"],
                patient_actor=osce["Patient_Actor"],
                physical_examination_findings=osce.get("Physical_Examination_Findings") or {},
                test_results=osce.get("Test_Results") or {},
                correct_diagnosis=osce["Correct_Diagnosis"],
                management_and_follow_up=osce.get("Management_and_Follow_Up"),
            )
        except (KeyError, ValueError) as exc:
            raise DatasetLoadError(f"{path}:{line_number} does not match the schema: {exc}") from exc

        cases.append(
            case.model_copy(
                update={
                    "dx_in_results": dx_in_results(
                        case.correct_diagnosis, case.test_results,
                        case.physical_examination_findings,
                    ),
                    "dx_tokens_in_results": dx_tokens_in_results(
                        case.correct_diagnosis, case.test_results,
                        case.physical_examination_findings,
                    ),
                }
            )
        )

    if expected is not None and len(cases) != expected:
        raise DatasetLoadError(
            f"{path}: expected {expected} cases but parsed {len(cases)}. "
            "The brief's case counts are facts to verify, not defaults."
        )
    return cases
