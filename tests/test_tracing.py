"""Tracing, including the judge/case separation that protects ground truth."""

from __future__ import annotations

import json

from agentclinic.tracing import Tracer


def read(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_writes_one_jsonl_per_case(tmp_path):
    t = Tracer(run_dir=tmp_path)
    t.node(case_id="medqa-0002", node="brief", event="enter")
    t.llm_call(case_id="medqa-0002", node="hypothesis", prompt_tokens=10,
               completion_tokens=5, cost=0.0001, latency_s=1.234)
    records = read(tmp_path / "traces" / "medqa-0002.jsonl")
    assert [r["kind"] for r in records] == ["node", "llm_call"]
    assert records[1]["latency_s"] == 1.234
    assert all("ts" in r for r in records)


def test_judge_records_never_enter_a_case_trace(tmp_path):
    """The judge is the only component that sees ground truth."""
    t = Tracer(run_dir=tmp_path)
    t.node(case_id="medqa-0002", node="brief", event="enter")
    t.judge(case_id="medqa-0002", match_type="exact", correct_diagnosis="Myasthenia gravis")
    case_text = (tmp_path / "traces" / "medqa-0002.jsonl").read_text()
    assert "Myasthenia gravis" not in case_text
    assert "Myasthenia gravis" in (tmp_path / "judge.jsonl").read_text()


def test_errors_record_type_and_message_but_never_a_traceback(tmp_path):
    t = Tracer(run_dir=tmp_path)
    try:
        raise ValueError("prompt contained: Myasthenia gravis")
    except ValueError as exc:
        t.error(case_id="medqa-0002", node="finalize", exc=exc)
    rec = read(tmp_path / "traces" / "medqa-0002.jsonl")[0]
    assert rec["error_type"] == "ValueError"
    assert "Traceback" not in json.dumps(rec)


def test_non_ascii_is_written_unescaped(tmp_path):
    """Four diagnoses contain curly apostrophes; escaping them breaks grep."""
    t = Tracer(run_dir=tmp_path)
    t.node(case_id="c", node="n", event="e", text="Hirschsprung’s disease")
    assert "Hirschsprung’s" in (tmp_path / "traces" / "c.jsonl").read_text(encoding="utf-8")
