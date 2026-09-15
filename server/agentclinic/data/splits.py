"""Deterministic dev / held-out splits, written once and never regenerated.

**Grouped, not stratified** (Q-36, D-024). The word matters: stratifying by a
label puts every label in *both* splits in proportion, which is the exact
opposite of what is wanted here. Grouping keeps each `Correct_Diagnosis` wholly
on one side, so a prompt tuned on a dev case cannot leak signal into held-out
through a duplicate. An earlier draft of the plan said "stratified", which would
have invited `StratifiedShuffleSplit` and created the leak permanently — the
split is committed once and never regenerated.

Grouping keys are **case-insensitive**: 31 diagnosis strings repeat across 63
cases as written, but 35 across 72 once normalised. Five diagnoses differ only
in capitalisation (`Cardiac Contusion` / `Cardiac contusion`, and four more) and
would otherwise be treated as distinct groups.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .models import Case

SEED = 20260915
DEV_SIZE = 40


class SplitError(RuntimeError):
    """The split could not be built to the exact declared size."""


def group_key(diagnosis: str) -> str:
    """Normalise a diagnosis for grouping: case-insensitive, whitespace-collapsed."""
    return " ".join(diagnosis.strip().lower().split())


def build_splits(cases: list[Case], *, seed: int = SEED, dev_size: int = DEV_SIZE) -> dict:
    """Return `{"dev": [...], "heldout": [...]}` as sorted case-id lists.

    Greedy fill over shuffled groups. `sorted(groups)` before the shuffle makes
    the result independent of dictionary insertion order, so the same cases in a
    different file order produce the same split.
    """
    groups: dict[str, list[str]] = {}
    for case in cases:
        groups.setdefault(group_key(case.correct_diagnosis), []).append(case.case_id)

    ordered = sorted(groups)
    random.Random(seed).shuffle(ordered)

    dev: list[str] = []
    for key in ordered:
        if len(dev) + len(groups[key]) <= dev_size:
            dev.extend(groups[key])
        if len(dev) == dev_size:
            break

    if len(dev) != dev_size:
        raise SplitError(
            f"greedy fill produced {len(dev)} dev cases, expected exactly {dev_size}. "
            "The group-size distribution has changed; do not write a partial split."
        )

    dev_set = set(dev)
    heldout = sorted(c.case_id for c in cases if c.case_id not in dev_set)
    return {"dev": sorted(dev), "heldout": heldout}


def serialise(splits: dict) -> str:
    """Canonical on-disk form, so the regeneration test has defined bytes."""
    return json.dumps(splits, sort_keys=True, indent=2) + "\n"


def write_splits(splits: dict, path: Path) -> None:
    """Write `splits.json`, refusing to overwrite an existing one.

    The brief requires splits be written once and never silently regenerated;
    making that a hard error is cheaper than trusting everyone to remember.
    """
    if path.exists():
        raise SplitError(
            f"{path} already exists. Splits are written once and never regenerated "
            "(brief §8). Delete it deliberately if you truly intend to re-split."
        )
    path.write_text(serialise(splits), encoding="utf-8")


def select_eval_subset(cases: list[Case], dev_ids: list[str], *, n: int = 3) -> list[str]:
    """Pick the fixed evaluation subset from dev (D-029).

    Rule: the lowest-`case_id` case flagged `dx_in_results`, then the
    lowest-`case_id` remaining cases until `n` are chosen. Deterministic, and it
    guarantees the leak path is exercised end to end — which matters because
    `dx_in_results` drives a headline breakdown in every report.

    The empty-`Test_Results` path is deliberately *not* required here: the dev
    split contains none of those four cases, and a unit test exercises all four
    for free rather than spending one of three precious encounters on it.
    """
    by_id = {c.case_id: c for c in cases}
    dev = sorted(dev_ids)
    flagged = [cid for cid in dev if by_id[cid].dx_in_results]
    if not flagged:
        raise SplitError("no dx_in_results case in dev; D-029's selection rule cannot be satisfied")

    chosen = [flagged[0]]
    for cid in dev:
        if len(chosen) == n:
            break
        if cid not in chosen:
            chosen.append(cid)
    return sorted(chosen)
