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

import re

from dataclasses import dataclass
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
    {mri, of, the, brain}. The synonym tier must honour the same direction; until
    D-057 it did not, and "MRI spine" returned `MRI_Brain` (M-09).
    """
    k = tokens(key)
    return bool(k) and k <= tokens(request)


#: Token spellings folded to the one the case keys use. "neurological exam"
#: missed every deterministic tier against `Neurological_Examination` and fell to
#: the LLM tier -- where a provider hiccup used to become a refusal (M-03).
_TOKEN_FOLD = {
    "exam": "examination", "exams": "examination", "examinations": "examination",
    "neurologic": "neurological", "neuro": "neurological",
}


def normalise(text: str) -> str:
    """Casefold, underscores and hyphens to spaces, drop punctuation, fold spellings.

    The docstring promised punctuation removal long before the code did it:
    "MRI (brain)." and "MRI of the brain, with contrast" kept their punctuation
    as part of a token and missed the deterministic tiers (M-23).
    """
    cleaned = str(text).replace("_", " ").replace("-", " ").casefold()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(_TOKEN_FOLD.get(w, w) for w in cleaned.split())


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
    #: True when the request bundled several things ("MRI brain and spinal
    #: cord") and the matched key covers only part of it (M-27).
    partial: bool = False

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
    #: result | partial | repeat | not_in_case -- feeds the ledger (D-056).
    outcome: str = "result"
    #: For a repeat: the turn the same record was first delivered.
    ref_turn: int | None = None


def load_synonyms() -> dict[str, dict[str, str]]:
    """Return `{domain: {normalised alias: normalised canonical}}`."""
    raw = yaml.safe_load(CONFIG_DIR.joinpath("test_synonyms.yaml").read_text())
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

    def _deterministic(self, request: str, domain: Domain) -> MatchResult | None:
        """Tiers that need no model: exact, contains, synonym, unique leaf."""
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
                               partial=_partial(wanted, contained[0]))

        # Tier 2 — curated synonyms. An alias may be a fragment of the request
        # ("CBC with differential"), so aliases are matched by containment too.
        table = self.synonyms.get(domain, {})
        canonical = table.get(wanted)
        if canonical is None:
            # Most specific alias wins, as the longest key wins in tier 1b:
            # "magnetic resonance imaging" beats a generic "imaging".
            hits = [(len(tokens(alias)), c) for alias, c in table.items() if contains(alias, wanted)]
            if hits:
                best = max(n for n, _ in hits)
                top_hits = {c for n, c in hits if n == best}
                canonical = top_hits.pop() if len(top_hits) == 1 else None
        if canonical:
            if canonical in top:
                key = top[canonical]
                return MatchResult(tier="synonym", key=key, payload=tree[key])
            # A key qualifies only if every one of its tokens is covered by the
            # request once the alias is expanded to its canonical name -- the
            # same direction as the contains tier. The old test also accepted a
            # key that merely *contained* the canonical, so a modality-only
            # canonical ("mri") matched any MRI the case had, whatever region
            # was asked for: "MRI spine" returned MRI_Brain, whose text names
            # the diagnosis (D-057). A key filed under an alias of the same
            # canonical ("LFTs" for "liver function tests") also qualifies.
            expanded = tokens(wanted) | tokens(canonical)
            by_canonical = [k for k in top
                            if tokens(k) <= expanded or table.get(k) == canonical]
            if len(by_canonical) == 1:
                key = top[by_canonical[0]]
                return MatchResult(tier="synonym", key=key, payload=tree[key],
                                   partial=_partial(wanted, by_canonical[0], canonical))

        # Granularity — a request naming a leaf matches its parent.
        parents = self._parents_with_leaf(domain, wanted)
        if len(parents) == 1:
            return MatchResult(tier="leaf", key=parents[0], payload=tree[parents[0]])
        return None

    def resolve_deterministic(self, request: str, domain: str = "tests") -> str | None:
        """The key a request resolves to without a model call, or None.

        Used by the repeat guard (D-056) to recognise a second order for a record
        already delivered. Returns a key name only -- never a payload -- so the
        orchestrator node that holds this function cannot render a result.
        """
        found = self._deterministic(request, domain)  # type: ignore[arg-type]
        return found.key if found is not None else None

    async def match(self, request: str, domain: Domain = "tests") -> MatchResult:
        """Resolve a request to at most one top-level key, recording the tier."""
        found = self._deterministic(request, domain)
        if found is not None:
            return found
        wanted = normalise(request)
        tree = self._tree(domain)

        parents = self._parents_with_leaf(domain, wanted)
        if len(parents) > 1:
            # D-035: ambiguous analyte. Return exactly one parent, never all.
            chosen = await self._ask_llm(request, tuple(parents))
            if chosen in tree:
                return MatchResult(tier="llm_disambiguated", key=chosen, payload=tree[chosen])
            return MatchResult(tier="unmatched")

        # Tier 3 — LLM over key names only. It never sees a value.
        chosen = await self._ask_llm(request, tuple(tree))
        if chosen in tree:
            return MatchResult(tier="llm", key=chosen, payload=tree[chosen])

        return MatchResult(tier="unmatched")

    async def _ask_llm(self, request: str, candidates: tuple[str, ...]) -> str | None:
        if not self.llm_disambiguate or not candidates:
            return None
        return await self.llm_disambiguate(request, candidates)

    async def respond(
        self, request: str, domain: Domain = "tests", delivered: dict[str, int] | None = None,
    ) -> GatekeeperReply:
        """Answer a request. Charges the cost whether or not anything matched.

        Q-12: an unlisted test still costs money. Free unavailability would let
        the doctor probe the case's key space at no price, and the cost-steward
        would never feel a wasted order.

        `delivered` maps keys already returned in this encounter to the turn they
        were returned. A second order for the same record gets a reply that says
        so, without the payload (M-08): medqa-0009 has two tests, and the doctor
        ordered nine, each time receiving a record it already had and concluding
        the detail it wanted was "unavailable despite multiple attempts".

        A failed model call in the LLM tier is **not** a refusal. It propagates to
        the node's harness guard (D-059); before that it was swallowed and the
        doctor was told the test "is not part of the case record at all" -- in
        c79bb4e6, two milliseconds after three 429s.
        """
        result = await self.match(request, domain)
        if result.matched:
            key = result.key or ""
            if delivered and key in delivered:
                return GatekeeperReply(
                    text=(f"This returns the same record already reported at turn "
                          f"{delivered[key]}. The case record holds nothing more specific "
                          "for this request."),
                    tier=result.tier, key=key, cost_usd=self.costs.price(key),
                    unlisted=False, outcome="repeat", ref_turn=delivered[key],
                )
            return GatekeeperReply(
                text=_render(result.key, result.payload),
                tier=result.tier,
                key=result.key,
                cost_usd=self.costs.price(key),
                unlisted=False,
                outcome="partial" if result.partial else "result",
            )
        return GatekeeperReply(
            text=UNAVAILABLE,
            tier="unmatched",
            key=None,
            cost_usd=self.costs.unknown_price,
            unlisted=True,
            outcome="not_in_case",
        )


#: Words that qualify a request rather than name a second thing, for the
#: bundled-request check.
_QUALIFIERS = frozenset(
    "with without and or of the a an for to in on contrast gadolinium iv test tests "
    "testing study studies scan scans imaging level levels panel full complete "
    "including plus also both left right bilateral repeat urgent stat please".split()
)
_CONJUNCTIONS = frozenset({"and", "plus", "also"})


def _partial(wanted: str, matched_key: str, canonical: str | None = None) -> bool:
    """True when a bundled request names something the matched key does not cover.

    Deliberately narrow: only a request containing a conjunction counts, and
    only leftover words that are not qualifiers. "MRI brain with and without
    contrast" is not partial; "MRI brain and spinal cord" is.
    """
    words = tokens(wanted)
    if not words & _CONJUNCTIONS:
        return False
    covered = tokens(matched_key) | (tokens(canonical) if canonical else frozenset())
    return bool(words - covered - _QUALIFIERS)


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


def make_llm_disambiguator(caller: Any, case_id: str) -> Any:
    """Build the cascade's tier-3 / ambiguous-leaf resolver.

    It is shown **key names only** — never a value. That matters: the matcher
    would otherwise be reading results it may not be allowed to return, and the
    brief forbids revealing unordered results.
    """
    template = CONFIG_DIR.joinpath("prompts", "gatekeeper_disambiguate.md").read_text(
        encoding="utf-8")

    from typing import Literal as _Literal

    from pydantic import create_model

    from ..llm.openrouter import StructuredOutputFailed

    async def disambiguate(request: str, candidates: tuple[str, ...]) -> str | None:
        prompt = template.format(
            candidates="\n".join(f"- {c}" for c in candidates), request=request
        )
        # The answer is one of the candidates or NONE, as a type -- an invented
        # key is now a validation error the repair loop can correct, rather than
        # a string silently compared away.
        choice_model = create_model(
            "Choice", entry=(_Literal[tuple(candidates) + ("NONE",)], ...),  # type: ignore[misc]
        )
        try:
            choice = await caller.structured(
                choice_model, prompt, case_id=case_id, node="gatekeeper"
            )
        except StructuredOutputFailed:
            # The model could not name a candidate: a genuine "no match".
            # Provider failures are NOT caught -- they propagate to the node's
            # harness guard, so an outage can never become a refusal (M-03).
            return None
        entry = str(getattr(choice, "entry", "")).strip()
        return entry if entry in candidates else None

    return disambiguate
