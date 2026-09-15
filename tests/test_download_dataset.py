"""Offline tests for the dataset download script.

Brief Section 11 requires download tests for checksum mismatch, line-count
mismatch, and network failure. Every test here drives the real code path
through an ``httpx.MockTransport``, so the suite never touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from download_dataset import (
    DatasetError,
    DatasetFile,
    count_nonblank_lines,
    download_dataset,
    sha256_bytes,
)

SPEC = DatasetFile("cases.jsonl", 3)


def make_jsonl(n_cases: int, *, trailing_blank: bool = False) -> bytes:
    """Build a JSONL body with ``n_cases`` non-blank lines.

    No trailing newline by default: the brief warns that the real files may
    lack one, and the counting logic must cope.
    """
    lines = [json.dumps({"OSCE_Examination": {"n": i}}) for i in range(n_cases)]
    body = "\n".join(lines)
    if trailing_blank:
        body += "\n\n   \n"
    return body.encode("utf-8")


def transport_serving(body: bytes, *, calls: list[str] | None = None) -> httpx.MockTransport:
    """A transport that returns ``body`` for any request, recording URLs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def client_for(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


def run(tmp_path: Path, transport: httpx.MockTransport, **kwargs) -> dict:
    with client_for(transport) as client:
        return download_dataset(
            dataset_dir=tmp_path, client=client, files=(SPEC,), **kwargs
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_downloads_file_and_records_provenance(tmp_path: Path) -> None:
    body = make_jsonl(3)
    manifest = run(tmp_path, transport_serving(body))

    assert (tmp_path / SPEC.name).read_bytes() == body

    entry = manifest["files"][SPEC.name]
    assert entry["sha256"] == sha256_bytes(body)
    assert entry["case_count"] == 3
    assert entry["commit_sha"] in entry["source_url"]
    assert entry["file_name"] == SPEC.name
    assert entry["downloaded_at"]

    on_disk = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert on_disk == manifest


def test_url_is_pinned_to_the_commit_sha(tmp_path: Path) -> None:
    calls: list[str] = []
    run(tmp_path, transport_serving(make_jsonl(3), calls=calls), commit_sha="deadbeef")

    assert calls == [
        "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/deadbeef/cases.jsonl"
    ]


def test_blank_lines_are_not_counted_as_cases(tmp_path: Path) -> None:
    body = make_jsonl(3, trailing_blank=True)
    manifest = run(tmp_path, transport_serving(body))

    assert manifest["files"][SPEC.name]["case_count"] == 3


def test_count_nonblank_lines_ignores_whitespace_only_lines() -> None:
    assert count_nonblank_lines(b'{"a":1}\n\n   \n{"b":2}') == 2


# ---------------------------------------------------------------------------
# Idempotency (D-001)
# ---------------------------------------------------------------------------


def test_second_run_makes_no_request(tmp_path: Path) -> None:
    body = make_jsonl(3)
    run(tmp_path, transport_serving(body))

    calls: list[str] = []
    run(tmp_path, transport_serving(body, calls=calls))

    assert calls == [], "an up-to-date file must not be re-fetched"


# ---------------------------------------------------------------------------
# D-004: recorded checksum differs
# ---------------------------------------------------------------------------


def test_checksum_mismatch_halts_without_modifying_the_file(tmp_path: Path) -> None:
    body = make_jsonl(3)
    run(tmp_path, transport_serving(body))

    tampered = body + b'\n{"OSCE_Examination": {"n": 99}}'
    (tmp_path / SPEC.name).write_bytes(tampered)

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, transport_serving(body))

    assert "does not match its recorded checksum" in str(exc.value)
    assert (tmp_path / SPEC.name).read_bytes() == tampered, "file must be untouched"


def test_checksum_mismatch_message_shows_both_hashes(tmp_path: Path) -> None:
    body = make_jsonl(3)
    run(tmp_path, transport_serving(body))
    (tmp_path / SPEC.name).write_bytes(make_jsonl(3) + b"\n")

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, transport_serving(body))

    message = str(exc.value)
    assert sha256_bytes(body) in message
    assert sha256_bytes(make_jsonl(3) + b"\n") in message


# ---------------------------------------------------------------------------
# D-012: file present, no manifest entry
# ---------------------------------------------------------------------------


def test_unrecorded_but_identical_file_is_adopted(tmp_path: Path) -> None:
    body = make_jsonl(3)
    (tmp_path / SPEC.name).write_bytes(body)

    manifest = run(tmp_path, transport_serving(body))

    assert manifest["files"][SPEC.name]["sha256"] == sha256_bytes(body)
    assert (tmp_path / SPEC.name).read_bytes() == body


def test_unrecorded_and_different_file_halts_without_overwriting(tmp_path: Path) -> None:
    local = make_jsonl(3) + b"\n{}"
    (tmp_path / SPEC.name).write_bytes(local)

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, transport_serving(make_jsonl(3)))

    assert "not recorded in MANIFEST.json" in str(exc.value)
    assert (tmp_path / SPEC.name).read_bytes() == local, "file must be untouched"


# ---------------------------------------------------------------------------
# Line-count mismatch
# ---------------------------------------------------------------------------


def test_line_count_mismatch_halts_and_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(DatasetError) as exc:
        run(tmp_path, transport_serving(make_jsonl(2)))

    assert "expected 3 cases" in str(exc.value)
    assert not (tmp_path / SPEC.name).exists(), "a bad file must never land"
    assert not (tmp_path / "MANIFEST.json").exists()


# ---------------------------------------------------------------------------
# Network failure
# ---------------------------------------------------------------------------


def test_connection_error_leaves_no_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, httpx.MockTransport(handler))

    assert "Network error" in str(exc.value)
    assert not (tmp_path / SPEC.name).exists()
    assert not (tmp_path / f"{SPEC.name}.part").exists()


def test_timeout_is_reported_clearly(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, httpx.MockTransport(handler))

    assert "Timed out" in str(exc.value)


def test_http_error_status_halts(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"Not Found")

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, httpx.MockTransport(handler))

    assert "HTTP 404" in str(exc.value)
    assert not (tmp_path / SPEC.name).exists()


# ---------------------------------------------------------------------------
# Manifest robustness
# ---------------------------------------------------------------------------


def test_malformed_manifest_halts_instead_of_being_overwritten(tmp_path: Path) -> None:
    (tmp_path / "MANIFEST.json").write_text("{ not json")

    with pytest.raises(DatasetError) as exc:
        run(tmp_path, transport_serving(make_jsonl(3)))

    assert "not valid JSON" in str(exc.value)
    assert (tmp_path / "MANIFEST.json").read_text() == "{ not json"
