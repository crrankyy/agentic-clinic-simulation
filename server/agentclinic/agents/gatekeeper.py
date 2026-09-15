"""The gatekeeper: turns a doctor's request into the matching case entries.

The matching problem is real, not incidental. The dataset has 234 distinct
top-level `Test_Results` keys across 214 cases, 165 of which appear exactly
once, with collisions like `Chest_X-ray`/`Chest_X-Ray`, `ECG`/`Electrocardiogram`
and `Blood_Tests`/`Blood_Work`/`Laboratory_Tests`/`Laboratory_Studies`. Exact
lookup would fail constantly, and every failure becomes a *fabricated*
unavailability that changes what the doctor believes.

Cascade (Q-11, D-034): exact → curated synonyms → leaf-matches-parent → LLM over
**key names only**. The fuzzy tier was dropped: its threshold had no value and
no tuning set, and its two candidate objectives trade off directly — a wrong-key
match hands the doctor results it never ordered, which brief §5.2 forbids.

Two rules that look like details and are not:

* A top-level match returns that key **and its complete sub-tree**. That is what
  ordering a panel means. It is not "revealing unordered results".
* An **ambiguous leaf** is disambiguated by the LLM tier and exactly one parent
  is returned (D-035). Verified: 58 of 214 cases have a leaf under more than one
  top-level key, and in 9 the duplicated leaf is a real analyte — `WBC` sits
  under both `Complete_Blood_Count` and `Urinalysis`. Returning both would
  disclose a urinalysis the doctor never ordered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml

from ..config import CONFIG_DIR, TestCosts
from ..data.views import GatekeeperView

MatchTier = Literal["exact", "contains", "synonym", "leaf",
                    "llm_disambiguated", "llm", "unmatched"]

Domain = Literal["tests", "exams"]

#: The refusal must distinguish "this case has no such result" from "you worded
#: the request badly", because to the doctor those are otherwise identical. In
#: the first live web run the doctor asked for a CSF JC virus PCR, was told
#: "Not available for this patient.", and re-asked the same test reworded on the
#: next turn -- spending 2 of its 8 turns to learn nothing. It says nothing
#: about *what* the case does hold: naming the key space would hand over a hint
#: the real task never gives, and on a single-test case that is close to naming
#: the answer.
UNAVAILABLE = (
    "Not available for this patient. This investigation is not part of the case "
    "record at all, so rewording the request will not retrieve it."
)


def tokens(text: str) -> frozenset[str]:
    """Normalised word set, for containment matching."""
    return frozenset(normalise(text).split())


def contains(key: str, request: str) -> bool:
    """True when every token of `key` appears in `request`.

    Deterministic and thresholdless, which is why this is not the fuzzy tier
    D-034 removed. It exists because real requests carry qualifiers the case
    keys do not: a doctor asks for "MRI of the brain with contrast" or "CBC with
    differential", while the case files them under `MRI_Brain` and
    `Complete_Blood_Count`. Requiring equality meant tiers 1 and 2 never fired
    once across a whole run — every match fell through to the LLM tier.

    Containment is directional on purpose. "MRI spine" does not match a request
    for "MRI of the brain", because {mri, spine} is not a subset of
    {mri, of, the, brain}.
    """
    k = tokens(key)
    return bool(k) and k <= tokens(request)


def normalise(text: str) -> str:
    """Casefold, underscores to spaces, collapse whitespace, drop punctuation runs."""
    cleaned = str(text).replace("_", " ").replace("-", " ")
    return " ".join(cleaned.casefold().split())


def _leaf_names(node: Any) -> Iterator[str]:
    """Yield the key of every leaf (non-container) value below `node`."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                yield from _leaf_names(value)
            else:
                yield str(key)
    elif isinstance(node, list):
        for value in node:
            yield from _leaf_names(value)


@dataclass(frozen=True)
class MatchResult:
    """What the gatekeeper found, and how."""

    tier: MatchTier
    key: str | None = None
    payload: Any = None
    candidates: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.key is not None


@dataclass
class GatekeeperReply:
    """What the doctor receives, plus the bookkeeping the runner needs."""

    text: str
    tier: MatchTier
    key: str | None
    cost_usd: float
    unlisted: bool


def load_synonyms(config_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Return `{domain: {normalised alias: normalised canonical}}`."""
    raw = yaml.safe_load((config_dir or CONFIG_DIR).joinpath("test_synonyms.yaml").read_text())
    table: dict[str, dict[str, str]] = {}
    for domain, entries in raw.items():
        mapping: dict[str, str] = {}
        for canonical, aliases in entries.items():
            mapping[normalise(canonical)] = normalise(canonical)
            for alias in aliases:
                mapping[normalise(alias)] = normalise(canonical)
        table[domain] = mapping
    return table


class Gatekeeper:
    """Holds a `GatekeeperView` and answers requests against it.

    The view is the *only* case data reachable from here — no objective, no
    patient history, and certainly no diagnosis.
    """

    def __init__(
        self,
        view: GatekeeperView,
        costs: TestCosts,
        synonyms: dict[str, dict[str, str]] | None = None,
        llm_disambiguate: Any = None,
    ) -> None:
        self.view = view
        self.costs = costs
        self.synonyms = synonyms if synonyms is not None else load_synonyms()
        #: Optional async callable (candidates, request) -> chosen key or None.
        #: Absent in offline tests, which is why tiers 4/5 degrade to `unmatched`.
        self.llm_disambiguate = llm_disambiguate

    def _tree(self, domain: Domain) -> dict[str, Any]:
        return (self.view.test_results if domain == "tests"
                else self.view.physical_examination_findings)

    def _top_level(self, domain: Domain) -> dict[str, str]:
        """`{normalised key: original key}` for the case's top-level entries."""
        return {normalise(k): k for k in self._tree(domain)}

    def _parents_with_leaf(self, domain: Domain, wanted: str) -> list[str]:
        """Top-level keys whose sub-tree contains a leaf named `wanted`."""
        return [
            original
            for original, value in self._tree(domain).items()
            if any(normalise(leaf) == wanted for leaf in _leaf_names({original: value}))
            and normalise(original) != wanted
        ]

    async def match(self, request: str, domain: Domain = "tests") -> MatchResult:
        """Resolve a request to at most one top-level key, recording the tier."""
        wanted = normalise(request)
        tree = self._tree(domain)
        top = self._top_level(domain)

        # Tier 1 — exact on the normalised top-level key.
        if wanted in top:
            key = top[wanted]
            return MatchResult(tier="exact", key=key, payload=tree[key])

        # Tier 1b — the key's tokens are all present in the request. Longest key
        # first, so "MRI brain" beats "MRI" when both would match.
        contained = sorted((k for k in top if contains(k, wanted)),
                           key=lambda k: (-len(tokens(k)), k))
        if len(contained) == 1 or (contained and
                                   len(tokens(contained[0])) > len(tokens(contained[1]))):
            key = top[contained[0]]
            return MatchResult(tier="contains", key=key, payload=tree[key],
                               candidates=tuple(top[c] for c in contained))

        # Tier 2 — curated synonyms. An alias may be a fragment of the request
        # ("CBC with differential"), so aliases are matched by containment too.
        table = self.synonyms.get(domain, {})
        canonical = table.get(wanted)
        if canonical is None:
            hits = {c for alias, c in table.items() if contains(alias, wanted)}
            canonical = hits.pop() if len(hits) == 1 else None
        if canonical:
            if canonical in top:
                key = top[canonical]
                return MatchResult(tier="synonym", key=key, payload=tree[key])
            by_canonical = [k for k in top if contains(k, canonical) or contains(canonical, k)]
            if len(by_canonical) == 1:
                key = top[by_canonical[0]]
                return MatchResult(tier="synonym", key=key, payload=tree[key])

        # Granularity — a request naming a leaf matches its parent.
        parents = self._parents_with_leaf(domain, wanted)
        if len(parents) == 1:
            return MatchResult(tier="leaf", key=parents[0], payload=tree[parents[0]])
        if len(parents) > 1:
            # D-035: ambiguous analyte. Return exactly one parent, never all.
            chosen = await self._ask_llm(request, tuple(parents))
            if chosen in tree:
                return MatchResult(tier="llm_disambiguated", key=chosen,
                                   payload=tree[chosen], candidates=tuple(parents))
            return MatchResult(tier="unmatched", candidates=tuple(parents))

        # Tier 3 — LLM over key names only. It never sees a value.
        chosen = await self._ask_llm(request, tuple(tree))
        if chosen in tree:
            return MatchResult(tier="llm", key=chosen, payload=tree[chosen])

        return MatchResult(tier="unmatched")

    async def _ask_llm(self, request: str, candidates: tuple[str, ...]) -> str | None:
        if not self.llm_disambiguate or not candidates:
            return None
        return await self.llm_disambiguate(request, candidates)

    async def respond(self, request: str, domain: Domain = "tests") -> GatekeeperReply:
        """Answer a request. Charges the cost whether or not anything matched.

        Q-12: an unlisted test still costs money. Free unavailability would let
        the doctor probe the case's key space at no price, and the cost-steward
        would never feel a wasted order.
        """
        result = await self.match(request, domain)
        if result.matched:
            return GatekeeperReply(
                text=_render(result.key, result.payload),
                tier=result.tier,
                key=result.key,
                cost_usd=self.costs.price(result.key or ""),
                unlisted=False,
            )
        return GatekeeperReply(
            text=UNAVAILABLE,
            tier="unmatched",
            key=None,
            cost_usd=self.costs.unknown_price,
            unlisted=True,
        )


def _render(key: str | None, payload: Any, indent: int = 0) -> str:
    """Flatten a matched sub-tree into plain text for the doctor.

    Handles `str | dict | list` at every level: 16 cases have string values at
    the top level of `Test_Results`, 2 contain lists, and
    `Physical_Examination_Findings` contains lists in 2 more.
    """
    pad = "  " * indent
    label = f"{pad}{str(key).replace('_', ' ')}:" if key is not None else ""
    if isinstance(payload, dict):
        inner = "\n".join(_render(k, v, indent + 1) for k, v in payload.items())
        return f"{label}\n{inner}" if label else inner
    if isinstance(payload, list):
        inner = "\n".join(f"{pad}  - {item}" if not isinstance(item, (dict, list))
                          else _render(None, item, indent + 1) for item in payload)
        return f"{label}\n{inner}" if label else inner
    return f"{label} {payload}".strip() if label else f"{pad}{payload}"


def make_llm_disambiguator(caller: Any, case_id: str, config_dir: Path | None = None) -> Any:
    """Build the cascade's tier-3 / ambiguous-leaf resolver.

    It is shown **key names only** — never a value. That matters: the matcher
    would otherwise be reading results it may not be allowed to return, and the
    brief forbids revealing unordered results.
    """
    from pydantic import BaseModel

    template = (config_dir or CONFIG_DIR).joinpath(
        "prompts", "gatekeeper_disambiguate.md"
    ).read_text(encoding="utf-8")

    class Choice(BaseModel):
        entry: str

    async def disambiguate(request: str, candidates: tuple[str, ...]) -> str | None:
        prompt = template.format(
            candidates="\n".join(f"- {c}" for c in candidates), request=request
        )
        try:
            choice = await caller.structured(
                Choice, prompt, case_id=case_id, node="gatekeeper"
            )
        except Exception:  # noqa: BLE001 — an unmatched request is a valid answer
            return None
        entry = choice.entry.strip()
        return entry if entry in candidates else None

    return disambiguate
