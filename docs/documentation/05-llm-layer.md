# 5. LLM layer

## Why everything funnels through `LLMCaller`

Three things must happen on **every** call, and none can be left to a node
author remembering:

1. the shared rate / daily / spend guards
2. cost accounting into the authoritative tracker (`D-039`)
3. a trace record

So nodes never hold a model. They hold a `caller`.

## The client — `llm/openrouter.py`

`Q-03`'s hybrid: LangChain's `ChatOpenAI` pointed at OpenRouter's
OpenAI-compatible endpoint — so `with_structured_output` and tool binding work
without reimplementation — plus a callback that captures OpenRouter's own
`usage` block, which LangChain does not surface and which is the only source of
real cost.

### Provider pinning

```yaml
provider:
  pin: ["Novita"]
  allow_fallbacks: false
```

Without a pin, OpenRouter may route the same model to different providers
between calls, with different tokenisers, latency and structured-output support.
A run whose provider changes mid-way is not one experiment.

### Structured output

`structured_output.method: function_calling` — forced tool calling rather than
native strict schemas or JSON mode. Chosen by **measuring against the real
nested schema**, which mattered: a flat-schema comparison flattered one model
that then scored 0/2 on the real one.

### The repair loop

`structured()` retries on invalid output, and distinguishes two failure modes
that need opposite responses:

```python
_EMPTY_SIGNATURES = ("'NoneType' object is not iterable",
                     "object of type 'NoneType' has no len()")

def _looks_empty(exc) -> bool:
    if isinstance(exc, (EmptyResponse, asyncio.TimeoutError, TimeoutError)):
        return True
    return any(s in str(exc) for s in _EMPTY_SIGNATURES)
```

- **Empty response** → back off and resend **unchanged**. The model said
  nothing; re-prompting it about its mistake is nonsense.
- **Invalid content** → re-prompt with the validation error.

The `_EMPTY_SIGNATURES` strings exist because a provider returned **HTTP 200 with
`choices: null`**, which surfaced as a `TypeError` from inside LangChain rather
than as any modelled error, and escaped the repair loop entirely.

### `asyncio.wait_for` on every call

```python
await asyncio.wait_for(coro, self.call_timeout)
```

**httpx timeouts are per-operation, not total.** A stream that trickles a byte
at a time resets the read timer forever, so `timeout=120` bounds nothing. This
was diagnosed from a **65-minute hang** — via `lsof` showing two ESTABLISHED
sockets and macOS `sample` showing the event loop pumping an async generator.
The first two hypotheses (a retry storm; the process having died) were both
wrong. (`D-044`)

### Per-invocation config

Callbacks are attached **per invocation**, not once on the model:

```python
chat.with_config(callbacks=[...]).with_structured_output(Model)   # ✗ drops callbacks
```

`with_structured_output` proxies to the underlying model and drops the config,
so `UsageRecorder` silently recorded zeros for an entire run.

## Guards — `llm/guards.py`

Three limits, because three separate things go wrong.

| Guard | Limit | Why it is shaped this way |
|---|---|---|
| `TokenBucket` | 18/min | The cap is **per account**, so one shared bucket is the only thing that can enforce it. Concurrency alone bursts past 20/min immediately. |
| `DailyRequestCounter` | 1000/day | **Persisted to disk.** The cap resets on a wall-clock day and a fresh process must not forget what an earlier one spent. Keyed on **UTC** — which mattered when local time rolled over five hours early. |
| `SpendTracker` | $0.50/case, $25/run | The **client** owns the authoritative total (`D-039`). State holds a snapshot that lags by at least one node, which would let the cap be exceeded silently. |

A breach raises; `budget_guarded` converts it into a clean finalize.

### Measured throughput

The free model delivers **4.7–8.5 requests/minute** against an 18/min bucket.
**The model is the bottleneck, not the limiter.** Raising concurrency does not
help.

The daily cap is what actually binds at scale:

| | calls/case | dev (40) | full (214) |
|---|---|---|---|
| `single_doctor` | ~41 | 1,630 | 8,800 |
| `panel` | ~77 | 3,090 | 16,500 |

Both arms on the dev split is roughly **five days** at 1000/day.

## Tracing — `llm/../tracing.py`

`runs/<run_id>/traces/<case_id>.jsonl`, plus `judge.jsonl`.

| Record | Contents |
|---|---|
| `llm_call` | node, tokens, cost, latency |
| `event` | one transcript entry with its `seq` |
| `node` | node transitions |
| `tool_call` | gatekeeper matching, etc. |
| `error` | **type and message only — never a traceback** |

Two rules are load-bearing rather than cosmetic:

- **Judge records never enter a per-case trace.** The judge is the only component
  that sees ground truth, and an exception from it can carry its prompt.
- **Errors record type and message, never a traceback.** A traceback carries
  local variables, and in the judge's case those include the answer.

The tracer opens, appends and closes per record, so a trace is readable **mid-run**
with no buffering lag. That is what makes live streaming and monitoring work.

### Event persistence — D-054

Encounter events used to live only in graph state, with `results.csv` as their
only sink. A crash in report generation therefore destroyed the behaviour
telemetry outright, and nothing could replay a run.

`Tracer.event()` now persists each entry with its `seq`, which makes the stream
replayable from an arbitrary offset and duplicate writes detectable.

## The fake model — `llm/fake.py`

`FakeChatModel` replays a scripted list of structured outputs. Every graph test
uses it, so **no test touches the network**.

It records `rendered_prompts`, which is how the isolation tests work: they assert
on what a node actually sent, not on what it was supposed to send. That is the
difference between testing the schema and testing the guarantee.
