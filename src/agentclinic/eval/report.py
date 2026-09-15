"""Renders `report.md`.

Two things the report must never do, both learned the hard way in review:

* print an accuracy without the denominator beside it — at n=3 a varying
  denominator is the difference between 1/1 and 1/3;
* print the leak-free breakdown without saying how many cases it covers. One of
  the three evaluation cases is a `dx_in_results` case by construction, so that
  breakdown is over **two**.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .metrics import calibration_bins, paired_bootstrap, summarise, wilson
from .runner import CaseResult

DISCLAIMER = (
    "> **Educational simulation only.** Not medical advice. These numbers "
    "describe a simulation over public benchmark data and say nothing about "
    "clinical performance."
)


@dataclass
class RunMetadata:
    run_id: str
    config_name: str
    model: str
    judge_model: str
    provider_pin: str
    fallbacks: bool
    cache_enabled: bool
    structured_output_mode: str
    enabled_actions: tuple[str, ...]
    max_turns: int
    split: str


def _accuracy_block(title: str, results: Sequence[CaseResult]) -> list[str]:
    summary = summarise([r.outcome for r in results], [r.judge_correct for r in results])
    lines = [f"### {title}", ""]
    if not results:
        return lines + ["(no cases)", ""]

    interval = wilson(summary.n_correct, summary.n_scored)
    lines.append(f"- **Accuracy (strict):** {summary.render()}"
                 + (f"  95% CI {interval}" if interval else ""))

    lenient = summarise([r.outcome for r in results], [r.lenient_correct for r in results])
    lines.append(f"- **Accuracy (lenient, broader/narrower count):** {lenient.render()}")

    coverage = summary.coverage
    lines.append(f"- **Coverage:** "
                 + (f"{coverage:.3f}" if coverage is not None else "n/a")
                 + f"  ({summary.n_scored} scored / {summary.n_scored + summary.n_abstained} "
                   "scored+abstained)")
    lines.append(f"- **Outcomes:** scored={summary.n_scored} abstained={summary.n_abstained} "
                 f"error={summary.n_error} crash={summary.n_crash} (n_total={summary.n_total})")

    scored = [r for r in results if r.outcome == "scored"]
    if scored:
        for k, attr in ((1, "top_1"), (3, "top_3"), (5, "top_5")):
            hits = sum(getattr(r, attr) for r in scored)
            lines.append(f"- **top-{k}:** {hits}/{len(scored)}")
        lines.append(f"- **in differential at all:** "
                     f"{sum(r.in_differential for r in scored)}/{len(scored)}")
    lines.append("")
    return lines


def render(
    results: Sequence[CaseResult],
    meta: RunMetadata,
    *,
    comparison: Sequence[CaseResult] | None = None,
    comparison_name: str = "single_doctor",
) -> str:
    lines: list[str] = [
        f"# Run {meta.run_id} — {meta.config_name}",
        "",
        DISCLAIMER,
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
    ]

    if len(results) < 10:
        lines += [
            "> ⚠️ **This run validates the harness; it does not measure anything.**",
            f"> With {len(results)} cases the attainable accuracies are a handful of "
            "discrete values, confidence intervals are not meaningful, and any",
            "> comparison between configurations cannot reach a conclusion in either "
            "direction. All figures below are dev-set figures, tuned and measured",
            "> on the same cases.",
            "",
        ]

    lines += _accuracy_block("All cases", results)

    leak_free = [r for r in results if not r.dx_in_results]
    flagged = [r for r in results if r.dx_in_results]
    lines += [
        "### Leak-free subset",
        "",
        f"{len(flagged)} of {len(results)} cases embed the exact diagnosis string in the "
        "test results the gatekeeper is designed to return. That is a property of the",
        "dataset, not a bug, and it cannot be fixed by isolation — so it is measured.",
        "",
    ]
    lines += _accuracy_block(f"Leak-free only (n={len(leak_free)})", leak_free)

    scored = [r for r in results if r.outcome == "scored"]
    lines += ["### Calibration", ""]
    if scored:
        bins = calibration_bins([r.final_confidence for r in scored],
                                [r.judge_correct for r in scored])
        lines += ["```"] + [b.render() for b in bins] + ["```", ""]
    else:
        lines += ["(no scored cases)", ""]

    if comparison is not None:
        arm_a = {r.case_id: r.judge_correct for r in results if r.outcome == "scored"}
        arm_b = {r.case_id: r.judge_correct for r in comparison if r.outcome == "scored"}
        diff = paired_bootstrap(arm_a, arm_b)
        cov_a = summarise([r.outcome for r in results], [r.judge_correct for r in results]).coverage
        cov_b = summarise([r.outcome for r in comparison],
                          [r.judge_correct for r in comparison]).coverage
        lines += [
            f"### {meta.config_name} minus {comparison_name}",
            "",
            f"- **Accuracy difference:** {diff.render()}",
            f"- **Coverage:** {meta.config_name}="
            + (f"{cov_a:.3f}" if cov_a is not None else "n/a")
            + f"  {comparison_name}=" + (f"{cov_b:.3f}" if cov_b is not None else "n/a"),
        ]
        if cov_a is not None and cov_b is not None and abs(cov_a - cov_b) > 1e-9:
            lines.append(
                "- ⚠️ **Coverages differ, so the accuracy difference is not "
                "interpretable.** Abstention-excluded accuracy rewards whichever arm "
                "abstains more."
            )
        lines.append("")

    lines += ["### Behaviour", ""]
    tiers: Counter[str] = Counter()
    for r in results:
        tiers.update(r.match_tiers)
    # A rebuilt run (D-050) never had its encounter events persisted, so most of
    # this block is unmeasured. Printing the dataclass defaults would present
    # "not recorded" as "observed zero" — the same failure the `n/a` handling
    # elsewhere in this report exists to avoid.
    rebuilt = [r for r in results if not r.behaviour_recovered]
    if rebuilt:
        lines += [
            f"> ⚠️ **{len(rebuilt)} of {len(results)} cases were reconstructed "
            "from `finals.json` and the traces (D-050), not measured live.** "
            "Encounter events are not persisted, so for those cases `turns` and "
            "`tests` are **lower bounds** — the gatekeeper only calls the model "
            "when its cheap match tiers miss — and exams, unlisted requests, "
            "match tiers, red flags and simulated cost were not recoverable at "
            "all. They are shown as `n/a`, not as zero.",
            "",
        ]
    partial = bool(rebuilt)
    na = "n/a (not recorded)"
    lines += [
        f"- turns: {sum(r.turns for r in results)}"
        + (" (lower bound)" if partial else "")
        + f" total, {sum(r.turns for r in results) / max(1, len(results)):.1f} mean",
        f"- forced stops: " + (na if partial else
                               f"{sum(r.forced_stop for r in results)}/{len(results)}"),
        f"- patient questions: {sum(r.patient_questions for r in results)}; "
        f"tests: {sum(r.tests_ordered for r in results)}"
        + (" (lower bound)" if partial else "")
        + "; exams: " + (na if partial else f"{sum(r.exams_requested for r in results)}"),
        "- unlisted requests: " + (na if partial else
                                   f"{sum(r.unlisted_tests for r in results)}"),
        "- gatekeeper match tiers: " + (na if partial else f"{dict(tiers) or '(none)'}"),
        "- simulated test cost: " + (na if partial else
                                     f"${sum(r.test_cost_usd for r in results):.2f} "
                                     "(illustrative prices, not a fee schedule)"),
        f"- parse failures: {sum(r.parse_failures for r in results)}",
        "",
    ]

    cost = sum(r.api_cost_usd for r in results)
    lines += [
        "### Run metadata",
        "",
        f"- agent model: `{meta.model}`  provider `{meta.provider_pin}` "
        f"fallbacks={meta.fallbacks}",
        f"- judge: `{meta.judge_model}`"
        + ("\n- ⚠️ **Verdicts were assigned by hand, not by a model.** They are a "
           "one-off reading at n=3, are not reproducible by re-running, and do not "
           "satisfy the judge-calibration requirement (D-036). No accuracy figure "
           "here should be compared against an automated run."
           if "manual" in meta.judge_model else ""),
        f"- enabled actions: {list(meta.enabled_actions)}  max turns: {meta.max_turns}",
        f"- split: {meta.split}  cache: {'on' if meta.cache_enabled else 'off'}  "
        f"structured output: {meta.structured_output_mode}",
        f"- API cost: " + (f"${cost:.4f}" if cost else "0.0 (free tier)"),
        "",
        "### Per-case",
        "",
        "| case | outcome | match | conf | turns | tests | unlisted | stop | leak |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.case_id} | {r.outcome} | {r.match_type or '—'} | {r.final_confidence:.2f} "
            f"| {r.turns} | {r.tests_ordered} | {r.unlisted_tests} | {r.stop_reason or '—'} "
            f"| {'yes' if r.dx_in_results else ''} |"
        )
    return "\n".join(lines) + "\n"


def write_report(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
