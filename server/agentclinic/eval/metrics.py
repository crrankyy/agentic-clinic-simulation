"""Scoring arithmetic.

Every edge case here is live at n=3, which is why each is spelled out rather
than left to whatever the code happens to do:

* The accuracy denominator **varies** — abstentions, API errors and crashes are
  all excluded (D-028, Q-08). So `n_scored` is printed beside every figure, and
  `n_scored == 0` prints `n/a`, never `0.0`.
* **Coverage** excludes errors and crashes from *both* terms: they are harness
  properties, not clinical judgements, and counting them as "not covered" would
  blame the doctor for a rate limit.
* Panel-minus-single accuracy is only interpretable at matched coverage.
  Abstention-excluded accuracy rewards whichever arm abstains more, and the
  challenger's entire job is to raise doubt — so the panel is systematically the
  more likely abstainer.
"""

from __future__ import annotations

import math
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
