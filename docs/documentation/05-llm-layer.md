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

### Model and provider — D-061

```yaml
provider:
  pin: ["Together"]
  allow_fallbacks: false
model: deepseek/deepseek-v4.1-flash   # one model for every role: no role/model confound
```

One key rather than a per-role map (D-064): the one-model rule (D-020) cannot
be broken by editing one line. Per-role `max_tokens` and reasoning effort live
in `role_settings`; a role not listed there uses `default`.

Without a pin, OpenRouter may route the same model to different providers
between calls, with different tokenisers, latency and structured-output support.
A run whose provider changes mid-way is not one experiment.

The provider was chosen by **`cli probe`**, which runs the pipeline's real
nested schemas against each candidate — the lesson from a model that passed a
flat schema and then scored 0/2 on the real one:

| Provider | Hypothesis | Orchestrator | Result |
|---|---|---|---|
| Together | 5-8 s | 4 s | 6/6 |
| Fireworks | 17 s | 11 s | 2/2 |
| DeepInfra | 23 s | 14 s | 2/2 |
| InferenceNet | 63-91 s | 36-59 s | 4/4 |
| DeepSeek, Parasail | — | — | refused by the account's training guardrail |

The earlier runs used `inclusionai/ling-3.0-flash-vl:free` on Novita (D-043).

### Per-role limits — D-061

`role_settings` in `models.yaml` gives each role its own `max_tokens` and
reasoning effort, and every role now actually gets its own settings — before,
one chat model built from the orchestrator's entry served them all (M-33).
Unbounded, hypothesis calls reached 32,768 completion tokens three times and
38,987 once, taking 488 s. A reply cut off at the limit is repaired with "be
more concise", not silently truncated.

### Structured output

`structured_output.method: function_calling` — forced tool calling rather than
native strict schemas or JSON mode. Chosen by **measuring against the real
nested schema**. DeepSeek also supports native structured outputs; switching
would be a deliberate decision, not a side effect of the model change.

### Failure handling — D-059

`structured()` classifies a failure **by what happened** — exception type and
HTTP status — not by how its message reads:

| Class | Examples | Response |
|---|---|---|
| auth | 401, 402, 403 | raise `ProviderAuthError` at once; run-fatal |
| config | 400, 404, 422 | raise `ProviderConfigError` at once; run-fatal |
| transient | 429, 5xx, connection, timeout, empty reply | jittered backoff, **resend unchanged**, shared cooldown on 429; then `ProviderUnavailable` |
| content | output that did not validate, a text reply instead of the tool call, a truncated reply | repair prompt with the error |

Before this, anything not string-matched as "empty" took the content path. A
401 or a 429 was re-sent within about a second **with the transport error
pasted into the doctor's prompt**, three times, then reported as "did not
validate" — which contradicted Q-08. The retry block in `budgets.yaml` was read
by nothing; it is now the policy.

Two further holes closed on the same path:

- **A text reply instead of the forced tool call** returns `None` from
  LangChain's parser rather than raising. It used to be handed to the node,
  which crashed on attribute access. It is now a content failure, repaired.
- **The "empty response" check** matched message strings from an earlier
  structured-output method; under forced tool calls, `choices: null` surfaces
  with a different message and was missed. The classifier covers both.

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

### Usage, per call

Callbacks are attached **per invocation**, not once on the model —
`with_structured_output` proxies to the underlying model and drops bound
config, so usage once silently read zero for an entire run.

Since D-059 each attempt also gets its **own** `UsageRecorder`. One shared
mutable slot was read after failures too, so every failed call re-added the
previous call's cost to the spend tracker — invisible at $0 on a free model,
wrong on a paid one — and concurrent cases could read each other's usage.

## Guards — `llm/guards.py`

Three limits, because three separate things go wrong.

| Guard | Limit | Why it is shaped this way |
|---|---|---|
| `TokenBucket` | 18/min free, 60/min paid | The cap is **per account**, so one shared bucket is the only thing that can enforce it. A 429 also sets a shared **cooldown**, so every worker waits, not only the one that was told to. |
| `DailyRequestCounter` | 1000/day, **free tier only** | Persisted to disk and keyed on UTC. It is a property of OpenRouter's `:free` tier (D-023); applying it to a paid model throttled paid runs by free-tier history (D-062). |
| `SpendTracker` | $0.50/case, $25/run | The **client** owns the authoritative total (`D-039`). One tracker **per run**: shared across web runs, the per-case cap became a lifetime cap per case id (M-35). |

A breach raises; `budget_guarded` converts it into a clean finalize.
`llm/factory.py` builds the caller and guards for both the CLI and the web
engine, so the two cannot drift apart again.

### Preflight — `llm/preflight.py`

Before a run, a zero-token check: is the key accepted and does it have spend
left (`GET /key`); does the pinned provider serve the model and advertise tool
calls (`GET /models/{model}/endpoints`); and, before a run starts, one tiny
**routed** completion. The listing cannot see account guardrails: it showed
first-party DeepSeek serving the model, and every real call was then refused.
A revoked key used to be discovered 0.9 s into a run, reported as a schema
failure.

### Measured throughput and cost

On the free model (the earlier runs) the model delivered **4.7–8.5
requests/minute** against an 18/min bucket, and the 1000/day cap was what bound
at scale: both arms on the dev split was about five days. On DeepSeek via
Together the 2026-09-27 sim cost **$0.027 for three cases**, with 24-33 s of
model time per case and no provider failures.

## Tracing — `llm/../tracing.py`

`runs/<run_id>/traces/<case_id>.jsonl`, plus `judge.jsonl`.

| Record | Contents |
|---|---|
| `llm_call` | node, tokens, cost, latency |
| `event` | one transcript entry with its `seq` |
| `node` | node transitions, and the runner's `case_end` |
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
