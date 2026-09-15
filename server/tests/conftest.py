"""Shared fixtures. The real dataset is used read-only; no test writes to it."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentclinic.data.loader import load_cases
from agentclinic.data.models import Case

from agentclinic.paths import DATASET_FILE as DATASET


@pytest.fixture(scope="session")
def cases() -> list[Case]:
    if not DATASET.exists():
        pytest.skip("dataset not downloaded; run scripts/download_dataset.py")
    return load_cases(DATASET)
