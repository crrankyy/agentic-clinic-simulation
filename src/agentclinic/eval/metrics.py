"""Scoring arithmetic.

Every edge case here is live at n=3, which is why each is spelled out rather
than left to whatever the code happens to do:

* The accuracy denominator **varies** — abstentions, API errors and crashes are
  all excluded (D-028, Q-08). So `n_scored` is printed beside every figure, and
  `n_scored == 0` prints `n/a`, never `0.0`.
* **Coverage** excludes errors and crashes from *both* terms: they are harness
  properties, not clinical judgements, and counting them as "not covered" would
  blame the doctor for a rate limit.
* The **paired bootstrap** is restricted to cases scored in *both* arms. A case
  the panel abstained on and the single doctor answered is not a pair.
* Panel-minus-single accuracy is only interpretable at matched coverage.
  Abstention-excluded accuracy rewards whichever arm abstains more, and the
  challenger's entire job is to raise doubt — so the panel is systematically the
  more likely abstainer.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal, Sequence

Outcome = Literal["scored", "abstained", "error", "crash"]

NA = "n/a"


@dataclass(frozen=True)
class Interval:
    low: float
    high: float

    def __str__(self) -> str:
        return f"[{self.low:.3f}, {self.high:.3f}]"


@dataclass(frozen=True)
class AccuracySummary:
    n_total: int
    n_scored: int
    n_abstained: int
    n_error: int
    n_crash: int
    n_correct: int

    @property
    def accuracy(self) -> float | None:
        """None — not zero — when nothing was scored."""
        return self.n_correct / self.n_scored if self.n_scored else None

    @property
    def coverage(self) -> float | None:
        denominator = self.n_scored + self.n_abstained
        return self.n_scored / denominator if denominator else None

    def render(self) -> str:
        if self.accuracy is None:
            return f"{NA} (n_scored=0)"
        suffix = " (n=1)" if self.n_scored == 1 else ""
        return f"{self.accuracy:.3f} (n_scored={self.n_scored}){suffix}"


def summarise(outcomes: Sequence[Outcome], correct: Sequence[bool]) -> AccuracySummary:
    """Build a summary from per-case outcomes and correctness flags."""
    if len(outcomes) != len(correct):
        raise ValueError("outcomes and correct must be the same length")
    scored = [c for o, c in zip(outcomes, correct) if o == "scored"]
    return AccuracySummary(
        n_total=len(outcomes),
        n_scored=len(scored),
        n_abstained=sum(1 for o in outcomes if o == "abstained"),
        n_error=sum(1 for o in outcomes if o == "error"),
        n_crash=sum(1 for o in outcomes if o == "crash"),
        n_correct=sum(1 for c in scored if c),
    )


def wilson(successes: int, n: int, z: float = 1.959963985) -> Interval | None:
    """Wilson score interval. Well-behaved at small n and near 0 or 1."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class PairedDifference:
    n_pairs: int
    difference: float | None
    interval: Interval | None

    def render(self) -> str:
        if self.difference is None:
            return f"{NA} (no case scored in both arms)"
        ci = f" 95% CI {self.interval}" if self.interval else ""
        return f"{self.difference:+.3f} over {self.n_pairs} paired cases{ci}"


def paired_bootstrap(
    arm_a: dict[str, bool], arm_b: dict[str, bool], *, resamples: int = 10_000, seed: int = 20260915
) -> PairedDifference:
    """Difference in accuracy over cases scored in **both** arms.

    Seeded, so a report is reproducible; the interval is a percentile interval
    over the paired differences.
    """
    shared = sorted(set(arm_a) & set(arm_b))
    if not shared:
        return PairedDifference(0, None, None)

    diffs = [int(arm_a[c]) - int(arm_b[c]) for c in shared]
    observed = sum(diffs) / len(diffs)

    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * resamples)]
    hi = means[min(int(0.975 * resamples), resamples - 1)]
    return PairedDifference(len(shared), observed, Interval(lo, hi))


@dataclass(frozen=True)
class CalibrationBin:
    low: float
    high: float
    count: int
    mean_confidence: float | None
    accuracy: float | None

    def render(self) -> str:
        if not self.count:
            return f"[{self.low:.1f}, {self.high:.1f})  n=0"
        return (f"[{self.low:.1f}, {self.high:.1f})  n={self.count}  "
                f"mean conf={self.mean_confidence:.3f}  accuracy={self.accuracy:.3f}")


def calibration_bins(
    confidences: Sequence[float], correct: Sequence[bool], *, bins: int = 10
) -> list[CalibrationBin]:
    """Equal-width bins over [0, 1]. Empty bins are returned, not omitted.

    Showing `n=0` makes sparsity visible; dropping empty bins makes a report
    from three cases look like a calibration curve.
    """
    width = 1.0 / bins
    out: list[CalibrationBin] = []
    for i in range(bins):
        low, high = i * width, (i + 1) * width
        members = [
            (c, ok) for c, ok in zip(confidences, correct)
            if (low <= c < high) or (i == bins - 1 and c == 1.0)
        ]
        if members:
            out.append(CalibrationBin(low, high, len(members),
                                      sum(c for c, _ in members) / len(members),
                                      sum(ok for _, ok in members) / len(members)))
        else:
            out.append(CalibrationBin(low, high, 0, None, None))
    return out
