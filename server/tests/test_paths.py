"""The repo-layout anchors.

Moving the package under `server/` silently retargeted every
`Path(__file__).parent.parent.parent` in the tree. The package's own anchors
raised loudly, but `conftest.py` had its own copy and simply stopped finding the
dataset — so 111 tests turned into skips and the suite still reported success.
A test suite that quietly stops testing is worse than one that breaks, so the
anchors are asserted directly.
"""

from __future__ import annotations

from agentclinic.paths import (
    CLIENT_DIR,
    CONFIG_DIR,
    DATASET_DIR,
    DATASET_FILE,
    REPO_ROOT,
    SERVER_DIR,
)


def test_every_anchor_points_at_something_real():
    assert (SERVER_DIR / "agentclinic").is_dir(), SERVER_DIR
    assert (CONFIG_DIR / "models.yaml").is_file(), CONFIG_DIR
    assert (CONFIG_DIR / "prompts").is_dir()
    assert DATASET_DIR.is_dir(), DATASET_DIR
    assert CLIENT_DIR.is_dir(), CLIENT_DIR
    assert REPO_ROOT.joinpath("pyproject.toml").is_file(), REPO_ROOT


def test_the_dataset_fixture_resolves_rather_than_skipping():
    """The specific regression: a moved anchor turned 111 tests into skips."""
    assert DATASET_FILE.is_file(), (
        f"{DATASET_FILE} is missing — every dataset-backed test would silently "
        "skip. Run server/scripts/download_dataset.py, or fix the anchor."
    )


def test_config_lives_under_server_and_data_at_the_root():
    """The split the layout depends on: config ships with the pipeline, data is
    shared with the client and with anything else that reads a run."""
    assert CONFIG_DIR.parent == SERVER_DIR
    assert DATASET_DIR.parent == REPO_ROOT
    assert SERVER_DIR.parent == REPO_ROOT
