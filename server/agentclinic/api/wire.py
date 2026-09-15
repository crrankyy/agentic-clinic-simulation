"""The wire format, and the isolation boundary the web app rests on.

The pipeline's governing invariant is that `Correct_Diagnosis` never reaches a
doctor-side channel, enforced by `CaseStore` having no method that returns a
whole `Case`. A spectator UI is a *different* audience — it is allowed to learn
the answer once the encounter is over — which is exactly why the boundary needs
restating here rather than assuming it carries over.

Two rules, and they are structural rather than conventions:

1. **`to_wire` is a whitelist.** It names the fields it copies. A future field
   on `Event` is invisible to the browser until someone adds it here, so the
   default for new state is "not transmitted".
2. **Ground truth travels on its own endpoint, never in the stream.** The
   reveal is built from `JudgeView` and is served only after the encounter has
   finished. It is never merged into an event payload, so no UI bug can render
   it early — the data simply is not in the browser yet.
"""

from __future__ import annotations

from typing import Any

#: Fields of `Event` that reach the browser. Deliberately explicit.
_EVENT_FIELDS = ("turn", "kind", "actor", "text")

#: `meta` keys the UI renders. `Event.meta` is a free-form dict written by
#: several nodes, so it is filtered rather than passed through: an unrecognised
#: key is dropped, not forwarded.
_META_KEYS = ("tier", "key", "unknown", "when", "cost_usd", "request")


def to_wire(event: Any, *, seq: int) -> dict[str, Any]:
    """One transcript entry, as the browser sees it."""
    meta = dict(getattr(event, "meta", {}) or {})
    return {
        "seq": seq,
        **{f: getattr(event, f) for f in _EVENT_FIELDS},
        "meta": {k: meta[k] for k in _META_KEYS if k in meta},
    }


def from_trace(record: dict[str, Any]) -> dict[str, Any]:
    """The same shape, rebuilt from a persisted trace record, for replay.

    Replay and live must be indistinguishable to the client, so this produces
    the identical payload rather than a parallel one.
    """
    meta = dict(record.get("meta") or {})
    return {
        "seq": record["seq"],
        "turn": record["turn"],
        "kind": record["event_kind"],
        "actor": record["actor"],
        "text": record["text"],
        "meta": {k: meta[k] for k in _META_KEYS if k in meta},
    }
