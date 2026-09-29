# 7. Web app

Watch an encounter unfold: the doctor questioning the patient, the gatekeeper
returning results, and on the panel configuration the challenger and cost
steward arguing with the doctor.

```bash
uv run python -m agentclinic.cli serve          # http://127.0.0.1:8000/
uv run python -m agentclinic.cli serve --port 8080
```

Binds to **loopback**. There is no authentication and starting an encounter
spends the account's OpenRouter allowance, so exposing it publicly would let
anyone drain it. Overriding `--host` prints a warning.

## Isolation across a new boundary — D-053

A spectator UI is a **different audience** from the doctor. It is allowed to
learn the answer once the encounter is over — which is exactly why the boundary
needed restating rather than assumed to carry over.

Three options were considered:

| | Approach | Why not |
|---|---|---|
| (a) | Send ground truth with the stream, let the UI hide it | Prompt-style isolation wearing a different hat — one render bug leaks it |
| (b) | Send it in the terminal frame | Still puts it in a transcript payload, so anything that logs or forwards a frame carries it |
| **(c)** | **Separate route, refused while running** | **Chosen** — the data is not in the browser at all until a separate, gated request is made |

### The wire format is a whitelist — `api/wire.py`

```python
_EVENT_FIELDS = ("turn", "kind", "actor", "text")
_META_KEYS = ("tier", "key", "unknown", "when", "cost_usd", "request", "action")

def to_wire(event, *, seq):
    meta = dict(getattr(event, "meta", {}) or {})
    return {"seq": seq,
            **{f: getattr(event, f) for f in _EVENT_FIELDS},
            "meta": {k: meta[k] for k in _META_KEYS if k in meta}}
```

`to_wire` **names** the fields it copies and filters `meta` by key. A future
field on `Event` is invisible to the browser until someone adds it here, so the
default for new state is *not transmitted*.

`from_trace` produces the **identical** shape for replay, so the client cannot
tell a replay from a live run. A test asserts the two are equal.

## Routes — `api/app.py`

| Route | Purpose |
|---|---|
| `GET /api/meta` | Model, provider pin, tier, allowance (free) or spend cap (paid), **whether the key works**, disclaimer |
| `GET /api/cases` | The three served cases — `case_id` and `objective` only |
| `GET /api/runs` | Live runs in this process + completed runs on disk |
| `POST /api/runs` | Start an encounter |
| `GET /api/runs/{id}/stream` | **SSE**, live or replayed |
| `GET /api/runs/{id}/reveal` | **Ground truth. The only route that has any.** 409 while running |

`/reveal` is also where the benchmark's own leak is surfaced: if the case is
`dx_in_results`, the response says so, and the UI shows that a correct answer
there is not evidence of reasoning.

### The served set

```python
SERVED_CASES = ("medqa-0002", "medqa-0009", "medqa-0012")
```

A test asserts this equals what `select_eval_subset` chooses, so if the dev
subset ever changes the suite fails rather than the app quietly serving
different cases than the runs it is compared against. (It used to re-derive the
split on every request; D-064 moved the check into the test.) An unserved case
is a **404, never a substitution**.

### Pre-flight

`POST /api/runs` first runs the credential and routing preflight
(`llm/preflight.py`) and refuses with **503** and the reason if it fails — the
page shows why Start is disabled. A revoked key used to be accepted and fail
0.9 s later, reported as "HypothesisUpdate did not validate". On the free tier
it also refuses with **429** if the projected worst case exceeds the remaining
daily allowance — the same projection `cli run` uses.

## The engine — `api/engine.py`

Owns the live-run registry and the account-wide limits. **The rate bucket and
daily counter are shared** — they are per account, so a per-run limiter would
let two concurrent runs breach both — but **the spend tracker is per run**:
shared, the $0.50 per-case cap became a lifetime cap per case id (M-35). A run
also has the CLI's case deadline, which the web path used to lack.

The judge is **never constructed here**. The reveal reads stored ground truth
directly; judging is an evaluation concern, and keeping it out of this path means
no judge prompt — and so no ground truth — exists anywhere near the streaming
code.

### Streaming

```python
async for chunk in graph.astream(..., stream_mode="values"):
    log = chunk.get("encounter_log") or []
    for i in range(seen, len(log)):
        tracer.event(case_id=..., event=log[i], seq=i)
        handle.append(to_wire(log[i], seq=i))
    seen = len(log)
```

`encounter_log` is append-only, so everything past the high-water mark is new.
**Emitting a diff rather than the whole log is what keeps replay idempotent.**

`stream_live` yields buffered events first, then new ones, then a terminal
status frame. Buffered-then-live is what lets a browser connect mid-run or
reconnect without missing anything — `start` is the client's high-water mark.

A failure is surfaced as a status frame with **type and message only**, never a
traceback.

## Replay

Completed runs replay from their traces through the identical channel and spend
no API allowance. This is the mode to use while working on the UI.

**Choose a model, then a run** (D-063). The model selector defaults to the model
you ran most recently; beneath it, that model's runs are listed newest first,
each run grouped with its cases and their outcome, as a list of buttons (D-065). Any case with a recorded
transcript replays — a 10-case run shows all ten — while starting a *live*
encounter stays limited to the three served cases. Each run's model, config and start
time come from `run.json`, written when the run starts; a run without one says
"model not recorded" rather than guess. A CLI run's stop reason is the runner's
`case_end` trace record. (Older runs were backfilled with these files once, so
the fallbacks that read them were removed in D-064.) `run_id` and `case_id` reach file
paths, so both are validated: a run must be a direct child of `runs/`, and a
case must have a trace in that run.

`summary.json` carries `status`, `stop_reason`, `error_class` and spend, and is
written on **every** exit — failures included (M-19). Replay used to report a
crashed or dead-key run as "Finished", and a capped one as finished
voluntarily; a run with no summary at all is now listed as `incomplete`. The
client turns `error_class` into plain language ("OpenRouter rejected the API
key"), where it used to show "did not validate".

> Runs recorded before **D-054** have no persisted transcript and are excluded
> from the replay list rather than shown as empty.

## The client — `client/`

Three files, no build step, no dependencies beyond Google Fonts: `index.html`,
`styles.css`, `app.js`, served as static files by the same FastAPI app. The
design is the handoff in `docs/design/encounter-viewer/` (D-065): `SPEC.md` is
the source of truth, and the boards and PNGs are its reference. Light theme and
desktop only for now.

### Identity keys off `kind`, not `actor`

`identify(ev)` maps each event to one of eight identities (Doctor, Patient,
Gatekeeper, Challenger, Cost steward, System, Alert, Repeat guard), a label, a
tag line and a template (row, record, compact line, advisor box, system rule,
alert band, guard band). **The challenger and cost steward emit with
`actor="doctor"`**, because they are sub-roles of the doctor side; styling by
actor alone would render them as the doctor talking to itself. `exam` and
`test` are the only kinds that also split on actor: from the doctor they are
an order, from the gatekeeper a result. Every identity pairs its colour with a
label and a glyph shape, so none depends on colour alone.

### What the UI shows

- A case selector with its referral objective, a Panel / Single doctor
  segmented control, max turns, and Start. When Start is blocked or refused,
  the reason sits in a box under the button: a rejected key (red), the free
  tier's allowance (amber), or the note that a run is streaming.
- The replay list, grouped by run, newest first.
- A status bar for every state: idle, running, replaying, finished, ended by a
  cap, failed with a plain-language reason, incomplete, connection lost,
  refused.
- The transcript, bottom-anchored and following the newest message. Scrolling
  up more than about 80px stops following and shows "Turn N of M" and a
  "Jump to latest" pill. Gatekeeper results show the matched record and its
  simulated test cost.
- A right column: a turn index with one glyph per event and the identity key;
  once the run ends, a compact index, a link to the final answer, and the
  **Ground truth** panel. That panel is not in the DOM until the stream's
  status event arrives, and the answer is fetched only on click.
- The final answer: diagnosis, confidence, rationale, red flags, and the ranked
  differential with a probability bar and a rationale per entry.

## Bugs this surfaced

Three, all found by running it rather than by testing it:

1. **`POST /api/runs` 500'd on every start.** A sync endpoint runs in FastAPI's
   threadpool, where `asyncio.create_task` raises "no running event loop". The
   engine tests missed it by calling `Engine.start()` from inside an async test,
   where a loop already exists.
2. **Replay reported the wrong stop reason**, because it was not persisted.
3. **The doctor re-ordered an unavailable test** — which led to `D-055`, and was
   only findable because the viewer shows the transcript the orchestrator does
   *not* see beside the summary it does.
