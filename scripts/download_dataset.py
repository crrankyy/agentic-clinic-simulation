"""Download and verify the AgentClinic MedQA case files.

Why this exists as a script rather than a one-off ``curl``: the dataset is the
ground truth for every evaluation number this project will produce, so its
provenance has to be reproducible and machine-checkable. The script is
idempotent, verifies what it fetched, records provenance in ``MANIFEST.json``,
and is testable offline with a mocked transport (see
``tests/test_download_dataset.py``).

Decisions this file implements (see ``docs/DECISIONS.md``):

* **D-002** — only the two MedQA files are fetched; no NEJM.
* **D-003** — the source is pinned to a commit SHA, never ``main``, so the data
  cannot change under a saved evaluation baseline.
* **D-004** — if a file exists and its hash differs from the recorded one, stop.
  Do not overwrite, do not delete: a mismatch can mean upstream changed, a local
  edit, or a corrupt download, and those need different human responses.
* **D-012** — if a file exists but has no manifest entry, fetch and compare.
  Identical bytes are adopted into the manifest; different bytes stop the run.

Usage::

    uv run python scripts/download_dataset.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

# --------------------------------------------------------------------------
# Pinned source (D-003)
# --------------------------------------------------------------------------

SOURCE_REPO = "https://github.com/SamuelSchmidgall/AgentClinic"
RAW_BASE_URL = "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic"

#: Upstream ``HEAD`` as of 2026-09-15. Changing this is a deliberate act: it
#: invalidates every recorded SHA-256 and any evaluation baseline measured
#: against the old data.
COMMIT_SHA = "b6570edefb940857a7c334350656b29f9d984f24"


@dataclass(frozen=True)
class DatasetFile:
    """One upstream file and the case count the brief says it must contain."""

    name: str
    expected_cases: int


#: D-002: the two MedQA files only.
DATASET_FILES: tuple[DatasetFile, ...] = (
    DatasetFile("agentclinic_medqa_extended.jsonl", 214),
    DatasetFile("agentclinic_medqa.jsonl", 107),
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_DIR = REPO_ROOT / "dataset"
MANIFEST_FILENAME = "MANIFEST.json"

#: Generous enough for a slow connection, short enough that a hung TCP
#: connection fails visibly instead of stalling the run. Overridable with
#: ``--timeout``. This is a Phase-0-local implementation choice, not an
#: architectural decision; the LLM-client timeouts in Section 4 are decided
#: separately.
DEFAULT_TIMEOUT_SECONDS = 30.0


class DatasetError(RuntimeError):
    """A verification failure that requires a human decision.

    Raised instead of "fixing" the problem automatically, because every
    situation that produces one (checksum drift, wrong case count, unknown
    provenance) has more than one plausible cause.
    """


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    """Return the hex SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_nonblank_lines(data: bytes) -> int:
    """Count lines that contain something other than whitespace.

    JSONL files often lack a trailing newline on the final line, and blank
    lines are not cases. Counting non-blank lines is therefore the only count
    that can be compared against the brief's expected case numbers.
    """
    return sum(1 for line in data.decode("utf-8").splitlines() if line.strip())


def build_url(commit_sha: str, filename: str) -> str:
    """Return the pinned raw.githubusercontent URL for one dataset file."""
    return f"{RAW_BASE_URL}/{commit_sha}/{filename}"


def load_manifest(manifest_path: Path) -> dict:
    """Read ``MANIFEST.json``, returning an empty manifest if absent.

    A malformed manifest is an error rather than something to overwrite: it
    usually means a partial write or a bad merge, and silently discarding it
    would destroy the provenance record it exists to protect.
    """
    if not manifest_path.exists():
        return {"source_repo": SOURCE_REPO, "commit_sha": COMMIT_SHA, "files": {}}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(
            f"{manifest_path} is not valid JSON ({exc}).\n"
            "Fix or delete it by hand, then re-run. It is not overwritten "
            "automatically because it is the only provenance record for the "
            "dataset."
        ) from exc
    manifest.setdefault("files", {})
    return manifest


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Write ``MANIFEST.json`` atomically, with a trailing newline."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".part")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)


def fetch(client: httpx.Client, url: str) -> bytes:
    """GET ``url`` and return its body, converting failures into DatasetError.

    No retries: a download either works or the user should see exactly why it
    did not. Retry/backoff policy is an LLM-client concern (brief Section 4),
    decided separately.
    """
    try:
        response = client.get(url)
    except httpx.TimeoutException as exc:
        raise DatasetError(f"Timed out fetching {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise DatasetError(f"Network error fetching {url}: {exc}") from exc

    if response.status_code != 200:
        raise DatasetError(
            f"Fetching {url} returned HTTP {response.status_code}.\n"
            "Check that the pinned commit SHA and file name are still valid "
            "upstream."
        )
    return response.content


def _verify_case_count(spec: DatasetFile, data: bytes, origin: str) -> int:
    """Check the non-blank line count against the brief, or raise."""
    actual = count_nonblank_lines(data)
    if actual != spec.expected_cases:
        raise DatasetError(
            f"{spec.name}: expected {spec.expected_cases} cases but {origin} "
            f"contains {actual} non-blank lines.\n"
            "The brief's case counts are facts to verify, not defaults. "
            "Stopping so this discrepancy can be reviewed."
        )
    return actual


def _entry(spec: DatasetFile, commit_sha: str, digest: str, cases: int) -> dict:
    """Build one MANIFEST.json record."""
    return {
        "file_name": spec.name,
        "source_url": build_url(commit_sha, spec.name),
        "commit_sha": commit_sha,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": digest,
        "case_count": cases,
    }


def _write_atomically(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` via a temporary file in the same directory.

    Guarantees the brief's "do not leave partial files" requirement: a crash
    mid-write leaves a ``.part`` file that is never mistaken for the real one.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Per-file logic
# --------------------------------------------------------------------------


def _process_file(
    spec: DatasetFile,
    *,
    dataset_dir: Path,
    manifest: dict,
    client: httpx.Client,
    commit_sha: str,
) -> tuple[dict, str]:
    """Bring one dataset file up to date. Returns (manifest entry, status)."""
    dest = dataset_dir / spec.name
    recorded = manifest["files"].get(spec.name)
    url = build_url(commit_sha, spec.name)

    if dest.exists():
        on_disk = sha256_path(dest)

        if recorded is not None:
            if recorded.get("sha256") == on_disk:
                # Re-verify the count rather than trusting the manifest, so a
                # hand-edited manifest cannot mask a bad file.
                _verify_case_count(spec, dest.read_bytes(), "the file on disk")
                return recorded, "up to date"

            # D-004: stop, do not overwrite, do not delete.
            raise DatasetError(
                f"{dest} does not match its recorded checksum.\n"
                f"  recorded: {recorded.get('sha256')}\n"
                f"  on disk:  {on_disk}\n"
                "Nothing was modified. This means upstream changed, the file "
                "was edited locally, or a download was corrupted -- each needs "
                "a different response, so this is left for a human to decide."
            )

        # D-012: present but unrecorded. Fetch and compare before touching it.
        data = fetch(client, url)
        fetched = sha256_bytes(data)
        if fetched == on_disk:
            cases = _verify_case_count(spec, data, "the downloaded file")
            return _entry(spec, commit_sha, fetched, cases), "adopted (already correct)"

        raise DatasetError(
            f"{dest} exists but is not recorded in {MANIFEST_FILENAME}, and its "
            "contents differ from the pinned upstream file.\n"
            f"  on disk:  {on_disk}\n"
            f"  upstream: {fetched}\n"
            "Nothing was modified. Move or delete the local file if you want "
            "the pinned version."
        )

    data = fetch(client, url)
    cases = _verify_case_count(spec, data, "the downloaded file")
    _write_atomically(dest, data)
    return _entry(spec, commit_sha, sha256_bytes(data), cases), "downloaded"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def download_dataset(
    *,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    client: httpx.Client | None = None,
    files: Sequence[DatasetFile] = DATASET_FILES,
    commit_sha: str = COMMIT_SHA,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Download, verify, and record every dataset file. Returns the manifest.

    ``client`` is injectable so tests can drive the whole flow through an
    ``httpx.MockTransport`` with no network access.
    """
    dataset_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset_dir / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path)

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)

    try:
        for spec in files:
            entry, status = _process_file(
                spec,
                dataset_dir=dataset_dir,
                manifest=manifest,
                client=client,
                commit_sha=commit_sha,
            )
            manifest["files"][spec.name] = entry
            print(f"  {spec.name:38s} {status:26s} {entry['case_count']:>4d} cases")
    finally:
        if owns_client:
            client.close()

    manifest["source_repo"] = SOURCE_REPO
    manifest["commit_sha"] = commit_sha
    save_manifest(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=DEFAULT_DATASET_DIR,
        help="Directory to download into (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    print(f"AgentClinic dataset -> {args.dataset_dir}")
    print(f"Pinned to {SOURCE_REPO} @ {COMMIT_SHA}\n")

    try:
        manifest = download_dataset(dataset_dir=args.dataset_dir, timeout=args.timeout)
    except DatasetError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    total = sum(entry["case_count"] for entry in manifest["files"].values())
    print(f"\nOK: {len(manifest['files'])} files, {total} cases total.")
    print(f"Provenance recorded in {args.dataset_dir / MANIFEST_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
