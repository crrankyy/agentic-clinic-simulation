"""Filesystem anchors, defined once.

Every path in the package used to be spelled `Path(__file__).parent.parent.parent`,
which encodes the package's depth in the repo at each call site. Moving the
package under `server/` would have silently retargeted every one of them — the
kind of break that produces a confusing "file not found" three layers away from
the edit. Depth is now counted in exactly one place.
"""

from __future__ import annotations

from pathlib import Path

#: `server/` — the pipeline and its configuration.
SERVER_DIR = Path(__file__).resolve().parent.parent
#: The repository root, which holds data and artefacts shared with the client.
REPO_ROOT = SERVER_DIR.parent

CONFIG_DIR = SERVER_DIR / "config"
DATASET_DIR = REPO_ROOT / "dataset"
RUNS_DIR = REPO_ROOT / "runs"
CLIENT_DIR = REPO_ROOT / "client"

DATASET_FILE = DATASET_DIR / "agentclinic_medqa_extended.jsonl"
