# Encounter viewer redesign: handoff spec

Source canvas: https://claude.ai/artifact/YZbUaTKEA18XxTyPak4TYK (private to its owner).

The files in `boards/` are the design source, one per artboard. They are HTML with inline styles plus a small
`renderVals()` block that holds the sample data. They need the canvas runtime to render, so read them as reference
markup: don't serve them or copy the `<x-dc>`, `<sc-for>` or `<sc-if>` tags. **All sample text, case ids, run ids,
costs and probabilities in them are illustrative.** Real values come from the API.

If `png/` exists next to this file, it holds exported pictures of the same boards.

## 1. Boards

| Board | Shows |
|---|---|
| `Main.dc.html` | Live panel run streaming at turn 4. Start and Replay are disabled with a reason. Challenger and Cost steward messages are mixed in. The right column shows the turn index and the identity key. |
| `Finished.dc.html` | Finished run. The final answer block closes the transcript. The ground-truth panel is revealed beside it, with the leak warning. |
| `LongReplay.dc.html` | 68-event, 20-turn replay ended by `turn_cap`. The user has scrolled up to turn 8, so it shows the context bar, "Jump to latest", the dense turn index and a hidden ground-truth panel. |
| `Failed.dc.html` | Free-tier run that failed with `rate_limited`: red status bar, provider-error band, "stream closed · no final answer". |
| `Idle.dc.html` | Empty state. Free tier over its allowance, so Start is disabled with the reason beside it. "no runs for this model". |
| `Identities.dc.html` | The 8 speaker identities and every message type, rendered. |
| `States.dc.html` | Every status bar state (all 9 failure reasons), the Start and cost-note variants, the abstained final answer and the 4 ground-truth panel states. |

## 2. Rules the design must keep

1. **Ground truth.** The ground-truth panel is not in the DOM until the stream's `status` event arrives, not even as a hidden placeholder. The reveal is only fetched when the user clicks. The answer never enters the transcript.
2. **Disclaimer.** It is always visible as a full-width band under the header. The text comes from `/api/meta` (`disclaimer`). The first sentence is bold.
3. **One stream at a time.** Start and Replay are disabled while streaming, with the note "A run is streaming. Start and Replay open again when it ends."
4. **Visible refusals.** A refused or blocked Start shows its reason in a box directly under or beside the button, never only a greyed-out button.
5. **Identity by kind.** Identity comes from event `kind` (plus `actor` only for exam/test), never from `actor` alone. Challenger and Cost steward always render as advisors.
6. **API data only.** No scores, avatars, per-message timestamps, vitals or correctness verdicts.

## 3. Layout (desktop, designed at 1440 × 1024)

```
┌ header 56px ─────────────────────────────────────────────────────────────────────────┐
├ disclaimer band (auto height) ───────────────────────────────────────────────────────┤
│ sidebar 368px   │ status bar 52px ───────────────────────────────────────────────────│
│ · New encounter │ transcript (flex, padding 0 36–44px,       │ right column          │
│ · Replay list   │ content max-width 800px,                   │ 232px idle/streaming  │
│   (scrolls)     │ bottom-anchored, follows newest)           │ 360px once ended      │
└─────────────────┴────────────────────────────────────────────┴───────────────────────┘
```

The page doesn't scroll; the transcript, replay list and right column scroll on their own. Below 1280px wide, only
the transcript shrinks (minimum 560px). The sidebar and right column keep their widths.

## 4. Tokens

**Colour**

| Token | Hex | Use |
|---|---|---|
| paper | `#F4F2EC` | page ground, transcript ground |
| surface | `#FBFAF7` | header, sidebar, status bar, right column |
| card | `#FFFFFF` | inputs, records, cards, list |
| band | `#E9E5DA` | disclaimer, current or in-view turn highlight |
| band-line | `#D6D1C4` | disclaimer border, turn rules, record border |
| line | `#DDD9CF` | panel borders, dividers |
| line-strong | `#CFC9BB` | inputs, counters, system rules |
| line-soft | `#E6E2D8`, `#EFECE4` | record header rule, list row dividers, bar track |
| selected row | `#EDE9DF` | replay list selection |
| ink / ink-2 / ink-3 / ink-4 | `#1E1D1A` / `#3A3832` / `#5E5A52` / `#6B675E` | text, secondary, muted, tags |
| disabled button | bg `#DDD9CF`, text `#6F6A5E` | |
| amber (caps, warnings) | `#7A4F00`; tint `#FBF1DE` or `#FBF6EA`; border `#9A6A00` or `#B8923F`; text `#3D2A00` or `#5C3B00` | |
| red (failures, alerts) | `#A3221B`; tint `#FBEFEC`; label text `#7E1A14` | |

**Identity colours:** Doctor `#23508F` (halo `#D5DFEE`, pill border `#C9D3E3`) · Patient `#8A4F0E` ·
Gatekeeper `#3D4756` · Challenger `#6E3591` · Cost steward `#1F6B5D` · System `#5E5A52` · Alert `#A3221B` ·
Repeat guard `#6F6A5E` (dashed border `#8C8678`).

**Type** (Google Fonts)

`https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap`

- **Source Serif 4** is for clinical reading: doctor and patient messages (16.5/1.55; patient italic; red flag 600), the final diagnosis (25/600), the rationale (15.5/1.6), the case objective preview (14), the empty-state headline (24/600) and the wordmark (21/600).
- **IBM Plex Sans** is the UI face: labels 12.5/600, body 13, advisor text 14.5/1.55, status label 14/600, section headings 11/600 uppercase with letter-spacing 0.09em.
- **IBM Plex Mono** is for tags (11), gatekeeper records (13/1.65, `white-space: pre-line`), ids, counters, the key/model line, system-message text and cost notes (12).

**Radii:** inputs and buttons 6 · cards and panels 8 · advisor boxes 14 · records 0 (square) · pills 999 · bands 4.
**Touch targets:** at least 44px (buttons, inputs, turn chips); replay list rows are 40px.

## 5. Speaker identities and message templates

Replace `PARTY` and `partyFor` in `client/app.js` with this mapping:

| Event | Identity | Label | Tag line | Template |
|---|---|---|---|---|
| `objective` | System | Referral | – | Row, sans text |
| `question` | Doctor | Doctor | asks the patient | Row, serif |
| `answer` | Patient | Patient | "doesn't know" when `meta.unknown` | Row, italic serif |
| `exam` / `test`, actor `doctor` | Doctor | Doctor | requests an exam / orders a test | Row, serif |
| `exam` from gatekeeper | Gatekeeper | Examination | matched: {tier} | Record |
| `test` from gatekeeper | Gatekeeper | Test result | matched: {tier} | Record |
| `unlisted_test` | Gatekeeper | Gatekeeper | not available for this case | Row, mono text |
| `hypothesis` | Doctor | Doctor | working differential | Compact line |
| `red_flag` | Doctor | Red flag | – | Row, serif 600 (**changed**: the current code styles it as an alert) |
| `challenge` | Challenger | Challenger | argues against the leader · scheduled, every third turn / · triggered before the doctor finalized | Advisor |
| `cost_objection` | Cost steward | Cost steward | – | Advisor |
| `guard` | Repeat guard | Repeat blocked | no turn used (+ `meta.action`) | Guard band |
| `stop` | System | Encounter ended | – | System rule |
| `budget` | System | Budget | – | System rule |
| `provider_error` | Alert | Provider error | – | Alert band |
| `parse_failure` | Alert | Parse failure | – | Alert band |
| unknown kind | System | the kind | – | System rule |

**Glyphs** are `aria-hidden` spans, placed before the label:

| Identity | Shape | CSS |
|---|---|---|
| Doctor | filled circle | `11×11; border-radius:50%; background:#23508F` |
| Patient | open ring | `11×11; border-radius:50%; border:2px solid #8A4F0E` |
| Gatekeeper | filled square | `11×11; background:#3D4756` |
| Challenger | diamond | `9×9; background:#6E3591; transform:rotate(45deg)` |
| Cost steward | triangle | `12×11; background:#1F6B5D; clip-path:polygon(50% 0,100% 100%,0 100%)` |
| System | bar | `12×3; background:#5E5A52` |
| Alert | octagon | `12×12; background:#A3221B; clip-path:polygon(30% 0,70% 0,100% 30%,100% 70%,70% 100%,30% 100%,0 70%,0 30%)` |
| Repeat guard | dashed square | `11×11; border:1.5px dashed #6F6A5E` |

**Templates** (see `Identities.dc.html`):

- **Row:** a 156px speaker column with the glyph and label (12.5/600 in the identity colour) and the tag below it (mono 11, ink-4, indented 20px). Then a 20px gap and the text. Padding is 9px vertical.
- **Record:** a Row whose text sits in a white box with a 1px `#D6D1C4` border and square corners. If the API sends them, a header strip reads `record · {record name}` on the left and `test cost ${cost}` on the right (mono 11). The body is mono 13 with `pre-line`.
- **Compact line** (`hypothesis`): aligned to the text column. It shows a 7px doctor dot, "Doctor", the mono tag "working differential", then the text in a pill (1px `#C9D3E3` border, radius 999).
- **Advisor:** no speaker column. The box starts at the text column (inset 176px), with a 1.5px identity-colour border, radius 14 and a white fill. Its header holds the glyph, the label, an outline "ADVISOR" pill (10.5, uppercase) and the tag. The body is sans 14.5.
- **System rule:** full width. The glyph, label (600) and mono text sit centred between two 1px `#CFC9BB` rules.
- **Alert band:** full width, 2px solid `#A3221B` border, `#FBEFEC` fill, radius 4. It has a 142px speaker column inside, then mono 13 text.
- **Guard band:** the same shape as the alert band, with a 1.5px dashed `#8C8678` border and no fill.
- **Turn divider:** `TURN N` in mono 11.5 uppercase, followed by a 1px rule, with 22px above. It gets `id="turn-N"`. The referral gets `id="referral"`.

## 6. Regions

**Header.** On the left, three tiny glyphs (doctor, patient, gatekeeper), the wordmark and "encounter viewer" in mono
after a hairline. On the right, a mono key line with a small outlined `KEY` or `FREE` tag:
`{model} · paid · cap $0.5/case · $1.10 left on key` or `{model} · 812/1000 requests left today`.

**New encounter.**
- Case select, with the objective previewed below it in serif 14.
- Doctor configuration as a 2-button segmented control with `aria-pressed`. It replaces the `<select id="config">` and keeps the values `panel` and `single_doctor`. The Panel button carries the sub-line "doctor, challenger, cost steward".
- Max turns as a number input with the hint "1–20".
- The Start button, with the cost note beneath it:
  - **OK:** mono grey text, "worst case ~143 requests".
  - **Over allowance:** Start disabled, with an amber box (triangle glyph) reading "worst case ~143 requests, over today's allowance" and the helper line "Fewer max turns or a single doctor lowers the worst case."
  - **Key rejected:** Start disabled, with a red box (octagon glyph) reading "Cannot start: {reason}".
  - **Streaming:** Start disabled, with the streaming note from rule 3.
  - Link the button to the reason with `aria-describedby`.

**Replay list.** This replaces `<select id="runs">`.
- The model select stays, ordered by most recently used.
- Below it is a bordered, scrollable `role="list"`.
- Each run gets a group header on a paper background. Line 1 is `{started} · {config} · {n} case(s)` (12.5/600); line 2 is the run id in mono 11.
- Each case is a 40px `<button aria-pressed>` laid out as a grid of three columns: case id (92px), state (glyph plus text) and "N events".
- The selected row gets a `#EDE9DF` background and 600 weight.
- The button below reads "Replay {case_id}" when a row is selected. The hint text stays under it.
- Empty state: a dashed box with a dashed ring and "no runs for this model".

State glyphs for the list:

| State | Glyph |
|---|---|
| finalize | filled ink dot |
| running | blue dot with halo |
| turn_cap, no_new_actions, budget_exhausted, request_cap | amber ring |
| parse_failure, provider_error, failed | red octagon |
| incomplete | dashed ring |

**Status bar.** This is `role="status"`. Each state has a glyph, a bold label and, where it applies, `· {model}` in mono. Muted secondary text follows, then `turn N` and `N events` counter chips on the right. See `States.dc.html`.

| State | Glyph | Secondary text |
|---|---|---|
| Idle | grey ring | nothing loaded |
| Running | blue dot with halo | live {config} encounter on {case_id} · via {provider} |
| Replaying | blue play triangle | replay of {case_id} · {config} · {run_id} |
| Finished | ink dot | – |
| Ended ({stop_reason}) | amber ring; bar tinted `#FBF6EA` | – |
| Failed | red octagon; the whole bar tinted `#FBEFEC` with a 1.5px `#A3221B` bottom border | the plain-language reason, as its own text (not joined with an em dash) |
| Incomplete | dashed ring | the run did not complete |
| Connection lost | ring with a gap | the transcript received so far stays on screen |
| Refused | red octagon, tinted like Failed | the server's reason |

Only show "via {provider}" if the API gives the provider. Keep the reason strings in `FAILURE`, but use the brief's
wording: "OpenRouter rejected the API key; replace it, or raise its spend limit."

**Transcript.**
- It is bottom-anchored and follows the newest message while streaming. A small "Streaming · the view follows the newest message" line (three fading doctor-blue dots) sits under the last message during a live run.
- If the user scrolls more than about 80px above the bottom, stop following. Show a context bar at the top of the transcript: "TURN 8 OF 20 · you scrolled up, so the view stopped following the newest message". Also show a floating pill button: "Jump to latest · turn {N}". Resume following when the user reaches the bottom.
- After a failure without a final answer, end the transcript with the centred rule "stream closed · no final answer for this run".
- Empty state: the 8 glyphs in a row, then the headline "Pick a case and start an encounter, or replay a past run." (serif 24/600) and "The doctor cannot see the diagnosis. Neither can this page, until the encounter is over." (15).

**Right column.**
- **Idle or streaming (232px):**
  - "Turns" index: a row per turn with the label, the event count and a glyph strip (one glyph per event, in order). The current turn gets a `#E9E5DA` background and "· live".
  - "Key": the 8 identities with a one-line role each.
- **Once ended (360px):**
  - The key goes, and a compact turn index takes its place. Use 4-column chips of at least 44px for runs of 8 turns or fewer. Use dense 25px rows for long runs, with the in-view turns highlighted.
  - A link to the final answer, "(abstained)" when it applies.
  - The **ground-truth panel**, pinned to the bottom so it sits level with the final answer.
- Every index row is a link to `#turn-N`.

**Final answer block** (appended on the `status` event when `final` is present; see `Finished.dc.html`).
- A divider reading "DOCTOR'S FINAL ANSWER" with a 2px ink rule.
- A white card (radius 8) laid out as a `1fr | 300px` grid.
- Left side:
  - "Diagnosis" in serif 25/600 doctor blue, followed by `confidence 0.85` in mono.
  - "Rationale" in serif 15.5.
  - A red-flags line: "Red flags · none raised during this encounter", or `{flag}, raised at turn N` for each.
- Right side, a ranked "Differential" of up to 8 entries:
  - Each entry has its number, name (the top one at 600) and probability in mono.
  - Below that is a 4px bar at width = p (minimum 2%), doctor blue on `#EFECE4`, then the per-entry rationale (11.5, ink-3).
- **Abstained:** the diagnosis reads "(abstained)" in grey italic serif, with a dashed "abstained" chip.

**Ground-truth panel** (see the bottom of `States.dc.html`).
- A white card with a 1.5px ink border and radius 8. The header reads "GROUND TRUTH", with "separate channel" or the state in mono on the right.
- **Hidden:** "Held back until the encounter ends, and served on a separate channel that the transcript never touches." and a primary button, "Reveal the answer".
- **Revealed:**
  - "Correct diagnosis" in serif 23/600.
  - "Doctor answered" with the doctor's diagnosis in doctor blue, for comparison. Don't compute or show a verdict.
  - The note "Ground truth from the benchmark. Not a clinical judgement."
  - "Management and follow-up" when the response has it; otherwise "none recorded for this case".
  - A secondary "Hide the answer" button.
- **Leak warning** (when `dx_in_results`): an amber box with a triangle glyph reading "The benchmark embeds this diagnosis in the test results the gatekeeper returns, so a correct answer here is not evidence of reasoning."
- **Refused:** a red box with an octagon glyph reading "Reveal refused: {reason}", and a "Try again" button. Show this in the panel, not the status bar.

## 7. Data the design uses that may need plumbing

Check the server for the exact field names. If a field isn't sent yet, render without it (never fake it) and list
what's missing:

- the simulated test cost and matched case-record name on gatekeeper `exam`/`test` events;
- whether a `challenge` was scheduled (every third turn) or triggered before finalizing;
- a rationale for each `final.differential` entry, and `final.red_flags` with the turn each was raised;
- the provider name and run id for the status text;
- management and follow-up text in the reveal response;
- the live run as a "running" row in the replay list. Today `refreshRuns` filters live running runs out, which is fine to keep.

## 8. Accessibility

- Use real `<button>`, `<label for>` and `<input>` elements.
- Put `aria-pressed` on the segmented control and the list rows.
- Mark the status bar `aria-live="polite"`.
- Glyphs are `aria-hidden`, since the label carries the meaning.
- Show `:focus-visible` as a 2px `#23508F` outline with a 2px offset.
- The colours above were chosen for at least 4.5:1 contrast on paper and white; keep it that way.
- No motion is required.

## 9. Don'ts

- No stock doctor photos, stethoscopes, medical crosses or heartbeat lines.
- No scores, avatars, per-message timestamps, vitals or correctness verdicts.
- Never style an event by `actor` alone.
- Never fetch, cache or render ground truth before the run ends and the user clicks Reveal.
