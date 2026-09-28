"""The action ledger and the repeat guard (D-056).

D-055 tried to stop the doctor re-ordering refused tests by *showing* it a
marker. It failed live: in `web-single_doctor-1491dcac` the marker was in the
orchestrator's summary at every one of the three re-orders (turns 7, 10, 14),
and truncation was ruled out. A rule the model can ignore is not a rule, so the
rule now lives here, in code:

* `build_ledger` derives, **mechanically** from `encounter_log`, what the doctor
  has already done and what came of it. No model writes it.
* `check_repeat` decides whether a proposed action repeats one. The
  orchestrator's decision model raises on a repeat, the caller's repair loop
  re-asks, and no turn is consumed -- the same mechanism that already rejects a
  valid-but-disabled action (C-3), so there is no new graph cycle.

Three kinds of repeat, each seen in the recorded runs:

1. **A test the case does not contain, reworded.** Refused requests are matched
   by content-token equality or subset: "CSF JC virus PCR" is a subset of the
   refused "CSF JC virus PCR and JC virus antibody testing".
2. **A record already delivered.** medqa-0009 has two tests; the doctor ordered
   nine and got the same records back. A request that resolves deterministically
   to a key already returned is rejected.
3. **A question already asked.** Repeated questions share 57-100% of their
   content words (overlap coefficient) in the recorded runs; distinct questions
   top out around 50%. The threshold is configurable and every block is logged
   with the turn it matched, so false positives are visible.

Isolation: the ledger holds the doctor's own request text and an outcome flag.
It never holds result or answer text, and `key` is never rendered.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ..llm.openrouter import GUARD_MARKER
from .state import DifferentialItem, Event, LedgerEntry

#: Default question-repeat threshold (overlap coefficient). Measured on the
#: recorded runs: repeats 0.57-1.00, distinct questions <= 0.50. Exactly 2/3,
#: not 0.67 -- several real repeats score 2/3, and 0.666... < 0.67.
QUESTION_OVERLAP = 2 / 3

_REQUEST_STOP = frozenset(
    "a an the of for and or with without to in on please test tests testing "
    "assay study studies repeat again another new urgent stat check order "
    "perform obtain get".split()
)
_QUESTION_STOP = frozenset(
    "a an the of for and or with without to in on is are was were be been have has "
    "had do does did you your any ever about if so what which how when why who "
    "there that this these those it its as at by from into such e g i me my we our "
    "can could would should will now also other currently".split()
)
#: Words that do not distinguish one diagnosis from another, for the
#: leader-unchanged signal.
_DX_GENERIC = frozenset(
    "disease syndrome disorder secondary due to associated related acute chronic "
    "primary infection lesion possible probable suspected likely type stage grade "
    "with without therapy induced of the and or in".split()
)

_NO_YIELD = frozenset({"not_in_case", "repeat", "could_not_answer"})


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", str(text).replace("_", " ").casefold()).split()


def request_tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _words(text) if w not in _REQUEST_STOP)


def question_tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _words(text) if w not in _QUESTION_STOP and len(w) > 1)


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# --- the ledger -------------------------------------------------------------------

def build_ledger(log: Iterable[Event]) -> list[LedgerEntry]:
    """What the doctor has done, derived from the transcript alone."""
    events = list(log)
    out: list[LedgerEntry] = []
    for i, e in enumerate(events):
        if e.actor != "doctor":
            continue
        if e.kind in ("test", "exam"):
            meta = dict(e.meta or {})
            # The gatekeeper's own reply is the fallback source of truth, so an
            # event written before the outcome flag existed (any run before
            # D-055) cannot turn a refusal into a "result received".
            reply = next((g for g in events[i + 1:] if g.actor == "gatekeeper"
                          and g.kind == e.kind and g.turn == e.turn), None)
            reply_meta = (reply.meta or {}) if reply is not None else {}
            outcome = meta.get("outcome")
            if outcome is None:
                if meta.get("unlisted") == "True" or reply_meta.get("tier") == "unmatched":
                    outcome = "not_in_case"
                else:
                    outcome = "result"
            key = meta.get("key") or reply_meta.get("key")
            ref = meta.get("ref_turn")
            out.append(LedgerEntry(
                turn=e.turn, kind=e.kind, request=e.text, outcome=outcome,  # type: ignore[arg-type]
                key=key if key not in (None, "", "None") else None,
                ref_turn=int(ref) if ref not in (None, "", "None") else None,
            ))
        elif e.kind == "question":
            answer = next((a for a in events[i + 1:]
                           if a.kind == "answer" and a.turn == e.turn), None)
            unknown = bool(answer and (answer.meta or {}).get("unknown") == "True")
            out.append(LedgerEntry(turn=e.turn, kind="question", request=e.text,
                                   outcome="could_not_answer" if unknown else "answered"))
    return out


def no_yield_streak(ledger: list[LedgerEntry]) -> int:
    """How many of the most recent actions produced nothing new."""
    n = 0
    for entry in reversed(ledger):
        if entry.outcome not in _NO_YIELD:
            break
        n += 1
    return n


def same_leader(a: str | None, b: str | None) -> bool:
    """Loose on purpose -- "PML" and "PML secondary to natalizumab" are one leader."""
    if not a or not b:
        return False
    ta = frozenset(_words(a)) - _DX_GENERIC
    tb = frozenset(_words(b)) - _DX_GENERIC
    return bool(ta & tb)


def leader_progress(previous: Any, differential: list[DifferentialItem],
                    turn: int) -> tuple[str | None, int | None]:
    """(leader, turn it has led since). Information only (D-060)."""
    leader = differential[0].diagnosis if differential else None
    if leader is None:
        return None, None
    prev_leader = getattr(previous, "leader", None)
    prev_since = getattr(previous, "leader_since_turn", None)
    if prev_since is not None and same_leader(prev_leader, leader):
        return leader, prev_since
    return leader, turn


# --- the guard --------------------------------------------------------------------

Resolver = Callable[[str, str], "str | None"]


def check_repeat(
    action: str,
    argument: str,
    ledger: list[LedgerEntry],
    *,
    resolve: Resolver | None = None,
    question_overlap: float = QUESTION_OVERLAP,
) -> str | None:
    """Return a rejection message when `action` repeats something done, else None."""
    if action in ("order_test", "request_exam"):
        kind = "test" if action == "order_test" else "exam"
        domain = "tests" if kind == "test" else "exams"
        mine = request_tokens(argument)
        if not mine:
            return None
        key = resolve(argument, domain) if resolve else None
        if key is not None:
            prior = next((e for e in ledger if e.kind == kind and e.key == key
                          and e.outcome in ("result", "partial", "repeat")), None)
            if prior is None:
                return None  # obtainable and not yet received
            return (f"{GUARD_MARKER}: you already received this result at turn "
                    f"{prior.ref_turn or prior.turn} (requested as \"{prior.request[:80]}\"). "
                    "Ordering it again returns the same record; the case holds nothing more "
                    "specific under this request. Choose a different action, or finalize.")
        for prior in ledger:
            if prior.kind != kind or prior.outcome != "not_in_case":
                continue
            theirs = request_tokens(prior.request)
            if theirs and mine <= theirs:
                return (f"{GUARD_MARKER}: \"{argument[:80]}\" was already requested at turn "
                        f"{prior.turn} (\"{prior.request[:80]}\") and is NOT part of this case "
                        "record. No wording will retrieve it. Choose a different action, or "
                        "finalize on the evidence you have.")
        return None

    if action == "ask_patient":
        mine = question_tokens(argument)
        if len(mine) < 3:
            return None
        best: tuple[float, LedgerEntry] | None = None
        for prior in ledger:
            if prior.kind != "question":
                continue
            theirs = question_tokens(prior.request)
            if len(theirs) < 3:
                continue
            score = overlap(mine, theirs)
            if best is None or score > best[0]:
                best = (score, prior)
        if best and best[0] >= question_overlap:
            prior = best[1]
            answered = ("could not answer it" if prior.outcome == "could_not_answer"
                        else "already answered it")
            return (f"{GUARD_MARKER}: this question repeats what you asked at turn {prior.turn} "
                    f"(\"{prior.request[:90]}\"), and the patient {answered}. The answer will "
                    "not change. Ask about something genuinely new, or act on what you know.")
    return None


# --- rendering ---------------------------------------------------------------------

_OUTCOME_TEXT = {
    "result": "result received",
    "partial": "result received -- one matching record; the rest of this request may not "
               "exist in the case",
    "not_in_case": "NOT IN THIS CASE -- no wording will retrieve it",
    "answered": "answered",
    "could_not_answer": "the patient could not answer",
}


def render_ledger(ledger: list[LedgerEntry]) -> list[str]:
    """Lines for the prompt. Never renders `key`, and never any result text."""
    lines: list[str] = []
    orders = [e for e in ledger if e.kind in ("test", "exam")]
    questions = [e for e in ledger if e.kind == "question"]
    if orders:
        lines.append("Tests and examinations already requested:")
        for e in orders:
            text = (f"same record already received at turn {e.ref_turn}"
                    if e.outcome == "repeat" else _OUTCOME_TEXT[e.outcome])
            lines.append(f"  - [turn {e.turn}] {e.kind}: {e.request[:140]} -- {text}")
    if questions:
        lines.append("Questions already asked (the patient's answer will not change):")
        for e in questions:
            lines.append(f"  - [turn {e.turn}] {e.request[:140]} -- {_OUTCOME_TEXT[e.outcome]}")
    return lines


def unavailable_lines(ledger: list[LedgerEntry]) -> list[str]:
    seen: set[frozenset[str]] = set()
    lines = []
    for e in ledger:
        if e.outcome != "not_in_case":
            continue
        toks = request_tokens(e.request)
        if toks in seen:
            continue
        seen.add(toks)
        lines.append(f"- {e.request[:140]} (requested turn {e.turn})")
    return lines
