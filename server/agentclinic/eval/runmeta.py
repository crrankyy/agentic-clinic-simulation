"""Run-level metadata, written once when a run starts.

Before this, a run directory did not say which model produced it: the web
viewer could not tell a DeepSeek run from a ling one, and neither could anyone
reading `runs/` later. Both the CLI and the web engine now write `run.json`.
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
        "model": models.for_role("orchestrator"),
        "provider_pin": list(models.provider.pin),
        "structured_output": models.structured_output_method,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_source": "recorded at run start",
        **extra,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_run_meta(run_dir: Path) -> dict[str, Any]:
    """Model and config for a run, from the best evidence available.

    run.json first; else the model the provider reported on the run's own call
    records; else the report's metadata line; else nothing -- never a guess.
    """
    path = run_dir / "run.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    for trace in sorted((run_dir / "traces").glob("*.jsonl")) if (run_dir / "traces").is_dir() else []:
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") == "llm_call" and record.get("model"):
                return {"model": record["model"], "model_source": "reported on the run's calls"}
    report = run_dir / "report.md"
    if report.exists():
        for line in report.read_text(encoding="utf-8").splitlines():
            if "agent model:" in line and "`" in line:
                return {"model": line.split("`")[1], "model_source": "the run's report"}
    return {}
