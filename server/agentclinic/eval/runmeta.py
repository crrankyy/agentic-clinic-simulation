"""Run-level metadata, written once when a run starts.

Before this, a run directory did not say which model produced it: the web
viewer could not tell a DeepSeek run from a ling one, and neither could anyone
reading `runs/` later. Both the CLI and the web engine now write `run.json`,
and it is the only source read back -- a run without one says nothing about its
model rather than having one guessed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_run_meta(run_dir: Path, *, config: str, models: Any, **extra: Any) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "config": config,
        "model": models.model,
        "provider_pin": list(models.provider.pin),
        "structured_output": models.structured_output_method,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    """A small JSON file this project wrote, or {} if it is missing or torn."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_run_meta(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "run.json")
