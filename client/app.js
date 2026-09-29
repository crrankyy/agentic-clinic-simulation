/* AgentClinic encounter viewer (docs/design/encounter-viewer/SPEC.md).
 *
 * Ground truth: the panel that can show it is not created until the stream's
 * `status` event arrives, and the reveal is fetched only when the user clicks.
 * The server refuses a reveal while a run is running (409) either way; the
 * transcript stream has no field that could carry it.
 */
'use strict';

const $ = (id) => document.getElementById(id);

/* --- tiny DOM helper ---------------------------------------------------------- */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  }
  return el;
}
const glyph = (id) => h('span', { class: 'g g-' + id, 'aria-hidden': 'true' });

/* --- identities (SPEC §5): kind decides, actor only for exam/test ------------ */
const KEY = [
  ['doctor', 'Doctor', 'asks, examines, orders tests'],
  ['patient', 'Patient', 'answers questions'],
  ['gate', 'Gatekeeper', 'returns results from the case record'],
  ['chall', 'Challenger', 'advisor · argues against the leader'],
  ['cost', 'Cost steward', 'advisor · objects to wasteful tests'],
  ['system', 'System', 'referral, end of encounter, budget'],
  ['alert', 'Alert', 'provider and parse failures'],
  ['guard', 'Repeat guard', 'blocked repeat, no turn used'],
];

const WHEN = {
  scheduled: 'scheduled, every third turn',
  pre_finalize: 'triggered before the doctor finalized',
};

function identify(ev) {
  const m = ev.meta || {};
  switch (ev.kind) {
    case 'objective': return { id: 'system', label: 'Referral', tpl: 'row', text: 't-sans' };
    case 'question': return { id: 'doctor', label: 'Doctor', tag: 'asks the patient', tpl: 'row', text: 't-serif' };
    case 'answer': return { id: 'patient', label: 'Patient', tag: m.unknown === 'True' ? "doesn't know" : '',
                            tpl: 'row', text: 't-serif t-italic' };
    case 'exam':
    case 'test':
      if (ev.actor === 'doctor') {
        return { id: 'doctor', label: 'Doctor', tpl: 'row', text: 't-serif',
                 tag: ev.kind === 'exam' ? 'requests an exam' : 'orders a test' };
      }
      return { id: 'gate', label: ev.kind === 'exam' ? 'Examination' : 'Test result', tpl: 'record',
               tag: m.tier ? 'matched: ' + m.tier : '' };
    case 'unlisted_test': return { id: 'gate', label: 'Gatekeeper', tag: 'not available for this case', tpl: 'row', text: 't-mono' };
    case 'hypothesis': return { id: 'doctor', label: 'Doctor', tag: 'working differential', tpl: 'compact' };
    case 'red_flag': return { id: 'doctor', label: 'Red flag', tpl: 'row', text: 't-serif t-bold' };
    case 'challenge': return { id: 'chall', label: 'Challenger', tpl: 'advisor',
                               tag: 'argues against the leader' + (WHEN[m.when] ? ' · ' + WHEN[m.when] : '') };
    case 'cost_objection': return { id: 'cost', label: 'Cost steward', tpl: 'advisor' };
    case 'guard': return { id: 'guard', label: 'Repeat blocked', tpl: 'guard',
                           tag: 'no turn used' + (m.action ? ' · ' + m.action : '') };
    case 'stop': return { id: 'system', label: 'Encounter ended', tpl: 'sysrule' };
    case 'budget': return { id: 'system', label: 'Budget', tpl: 'sysrule' };
    case 'provider_error': return { id: 'alert', label: 'Provider error', tpl: 'alert' };
    case 'parse_failure': return { id: 'alert', label: 'Parse failure', tpl: 'alert' };
    default: return { id: 'system', label: ev.kind, tpl: 'sysrule' };
  }
}

function speaker(p) {
  return h('div', { class: 'speaker' },
    h('span', { class: 'who c-' + p.id }, glyph(p.id), h('span', { text: p.label })),
    p.tag ? h('span', { class: 'tag', text: p.tag }) : null);
}

const money = (v) => '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function renderMessage(ev) {
  const p = identify(ev);
  const m = ev.meta || {};
  switch (p.tpl) {
    case 'record': {
      const named = m.key && m.key !== 'None';
      return h('div', { class: 'msg-row' }, speaker(p),
        h('div', { class: 'record' },
          named ? h('div', { class: 'record-head' },
            h('span', { text: 'record · ' + m.key }),
            m.cost_usd ? h('span', { text: 'test cost ' + money(m.cost_usd) }) : null) : null,
          h('p', { class: 'record-body', text: ev.text })));
    }
    case 'compact':
      return h('div', { class: 'compact' }, h('span', { class: 'pad' }),
        h('p', {}, h('span', { class: 'dot7', 'aria-hidden': 'true' }), h('span', { class: 'who', text: 'Doctor' }),
          h('span', { class: 'tag', text: p.tag }), h('span', { class: 'pill', text: ev.text })));
    case 'advisor':
      return h('div', { class: 'advisor' },
        h('div', { class: 'advisor-box c-' + p.id },
          h('span', { class: 'advisor-head' }, glyph(p.id), h('span', { class: 'lab', text: p.label }),
            h('span', { class: 'adv-pill', text: 'advisor' }), p.tag ? h('span', { class: 'tag', text: p.tag }) : null),
          h('p', { text: ev.text })));
    case 'sysrule':
      return h('div', { class: 'sysrule' }, h('span', { class: 'rule' }),
        h('span', { class: 'mid' }, glyph(p.id), h('span', { class: 'lab c-' + p.id, text: p.label }),
          h('span', { class: 'txt', text: ev.text })),
        h('span', { class: 'rule' }));
    case 'alert':
    case 'guard':
      return h('div', { class: 'band-wrap' },
        h('div', { class: 'band ' + p.tpl, role: p.tpl === 'alert' ? 'alert' : null }, speaker(p),
          h('p', { class: 'msg-text', text: ev.text })));
    default:
      return h('div', { class: 'msg-row' }, speaker(p), h('p', { class: 'msg-text ' + p.text, text: ev.text }));
  }
}

/* --- state ------------------------------------------------------------------------- */
let meta = null;              // last /api/meta
let config = 'panel';
let allRuns = [];
let selected = null;          // {run_id, case_id}
let refusal = null;           // the server's reason the last Start was refused
let source = null;            // open EventSource
let current = null;           // {source: 'live'|'replay', run_id, case_id, config, model, provider, maxTurns}
let streaming = false;
let ended = null;             // the status event, once it has arrived
let following = true;
let lastScrollTop = 0;
let turns = new Map();        // turn -> {n, ids: [], alert, group, divider}
let lastTurn = null;
let eventCount = 0;
let inView = new Set();
let observer = null;
let gt = null;                // ground-truth panel state; null until the run ends

const FAILURE = {
  auth: 'OpenRouter rejected the API key; replace it, or raise its spend limit.',
  config: 'OpenRouter could not serve this model/provider/parameter combination.',
  rate_limited: 'The provider kept rate-limiting after every retry.',
  server_error: 'The provider kept returning server errors after every retry.',
  timeout: 'The provider kept timing out after every retry.',
  connection: 'Could not connect to the provider.',
  empty: 'The provider kept returning empty responses.',
  budget: 'A spend or request cap was reached.',
  deadline: 'The encounter hit its wall-clock deadline.',
};
const CAP_STOPS = ['turn_cap', 'no_new_actions', 'budget_exhausted', 'request_cap'];
const STREAMING_NOTE = 'A run is streaming. Start and Replay open again when it ends.';

const shortModel = (m) => (m ? m.split('/').pop() : 'model not recorded');
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const provider = (pin) => (Array.isArray(pin) && pin.length ? pin.join(', ') : null);

function api(path, opts) {
  return fetch(path, opts).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  });
}

/* --- header -------------------------------------------------------------------------- */
function renderHeader() {
  if (!meta) return;
  const tag = $('keyTag');
  tag.hidden = false;
  tag.textContent = meta.tier === 'free' ? 'free' : 'key';
  const left = (meta.credential || {}).key_limit_remaining;
  $('keyLine').textContent = meta.tier === 'free'
    ? `${shortModel(meta.model)} · ${meta.daily_remaining}/${meta.daily_limit} requests left today`
    : `${shortModel(meta.model)} · paid · cap $${meta.spend_cap_per_case_usd}/case` +
      (left !== null && left !== undefined ? ` · $${Number(left).toFixed(2)} left on key` : '');
  // The first sentence is bold (SPEC rule 2).
  const text = meta.disclaimer || '';
  const cut = text.indexOf('. ');
  $('disclaimer').replaceChildren(
    h('strong', { text: cut >= 0 ? text.slice(0, cut + 1) : text }),
    cut >= 0 ? text.slice(cut + 1) : '');
}

/* --- new encounter ---------------------------------------------------------------------- */
function projected() {
  const n = Number($('turns').value) || 20;
  return n * (config === 'panel' ? 7 : 3) + 3;
}

function updateStartControls() {
  const start = $('start');
  const reason = $('startReason');
  const cred = (meta && meta.credential) || { ok: true };
  const remaining = meta && meta.tier === 'free' ? meta.daily_remaining : null;
  const cost = `worst case ~${projected()} requests`;
  let disabled = false;
  $('startInline').textContent = '';
  reason.replaceChildren();

  if (streaming) {
    disabled = true;
    $('startInline').textContent = STREAMING_NOTE;
    reason.append(h('span', { class: 'cost-note', text: cost }));
  } else if (cred.ok === false) {
    disabled = true;
    reason.append(h('div', { class: 'reason-box red' }, glyph('alert'), h('span', { text: 'Cannot start: ' + cred.reason })));
  } else if (refusal) {
    reason.append(h('div', { class: 'reason-box red' }, glyph('alert'), h('span', { text: 'Cannot start: ' + refusal })));
  } else if (remaining !== null && remaining !== undefined && projected() > remaining) {
    disabled = true;
    reason.append(h('div', { class: 'reason-box amber' }, h('span', { class: 'g g-amber-tri', 'aria-hidden': 'true' }),
      h('span', {}, h('span', { class: 'mono', text: `${cost}, over today's allowance` }),
        h('div', { class: 'help', text: 'Fewer max turns or a single doctor lowers the worst case.' }))));
  } else {
    reason.append(h('span', { class: 'cost-note', text: cost }));
  }
  start.disabled = disabled;
  document.querySelectorAll('.segmented button').forEach((b) => { b.disabled = streaming; });
  $('turns').disabled = streaming;
  $('case').disabled = streaming;
}

function setConfig(value) {
  config = value;
  document.querySelectorAll('.segmented button').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.config === value));
  });
  refusal = null;
  updateStartControls();
}

/* --- replay list ------------------------------------------------------------------------ */
function fmtStarted(iso) {
  if (!iso) return 'time not recorded';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const pad = (n) => String(n).padStart(2, '0');
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()];
  return `${d.getDate()} ${month}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function rowState(r) {
  if (r.status === 'finished') return r.stop_reason || 'finalize';
  return r.status;
}

function stateClass(state) {
  if (state === 'finalize' || state === 'finished') return 'finalize';
  if (state === 'running') return 'running';
  if (state === 'incomplete') return 'incomplete';
  if (CAP_STOPS.includes(state)) return 'cap';
  return 'fail';
}

function renderModelOptions() {
  const pick = $('modelFilter');
  const keep = pick.value;
  const seen = new Map();
  allRuns.forEach((r) => {
    const k = r.model || '';
    if (!seen.has(k)) seen.set(k, new Set());
    seen.get(k).add(r.run_id);
  });
  pick.replaceChildren();
  seen.forEach((ids, model) => pick.append(h('option', { value: model, text: `${shortModel(model || null)}, ${plural(ids.size, 'run')}` })));
  // The configured model is listed even with no runs, so its empty state is reachable.
  if (meta && !seen.has(meta.model)) pick.append(h('option', { value: meta.model, text: shortModel(meta.model) }));
  if ([...pick.options].some((o) => o.value === keep)) pick.value = keep;
}

function renderRunList() {
  const list = $('runList');
  const model = $('modelFilter').value;
  const rows = allRuns.filter((r) => (r.model || '') === model);
  const byRun = new Map();
  rows.forEach((r) => {
    if (!byRun.has(r.run_id)) byRun.set(r.run_id, []);
    byRun.get(r.run_id).push(r);
  });
  if (selected && !rows.some((r) => r.run_id === selected.run_id && r.case_id === selected.case_id)) selected = null;

  list.replaceChildren();
  if (!byRun.size) {
    list.append(h('div', { class: 'runlist-empty' }, h('span', { class: 'ring', 'aria-hidden': 'true' }), 'no runs for this model'));
  }
  byRun.forEach((cases, runId) => {
    const first = cases[0];
    list.append(h('div', { role: 'listitem' },
      h('div', { class: 'run-head' },
        h('span', { class: 'h', text: `${fmtStarted(first.started)} · ${first.config} · ${plural(cases.length, 'case')}` }),
        h('span', { class: 'id', text: runId })),
      cases.map((r) => {
        const state = rowState(r);
        const cls = stateClass(state);
        const isSel = !!selected && selected.run_id === r.run_id && selected.case_id === r.case_id;
        return h('button', {
          type: 'button', class: 'case-row', 'aria-pressed': String(isSel),
          disabled: streaming || r.status === 'running',
          onclick: () => { selected = { run_id: r.run_id, case_id: r.case_id }; renderRunList(); },
        },
        h('span', { text: r.case_id }),
        h('span', { class: 'state st-' + cls }, h('span', { class: 'sgl ' + cls, 'aria-hidden': 'true' }), h('span', { text: state })),
        h('span', { class: 'ev', text: plural(r.events, 'event') }));
      })));
  });
  const btn = $('replay');
  btn.textContent = selected ? `Replay ${selected.case_id}` : 'Replay';
  btn.disabled = streaming || !selected;
}

function refreshRuns() {
  return api('/api/runs').then((rows) => {
    allRuns = rows;
    renderModelOptions();
    renderRunList();
  }).catch(() => {});
}

function refreshMeta() {
  return api('/api/meta').then((m) => {
    meta = m;
    renderHeader();
    renderModelOptions();
    renderRunList();
    updateStartControls();
  }).catch(() => {});
}

/* --- status bar ----------------------------------------------------------------------------- */
function setStatus({ glyph: g, label, model, text, tone }) {
  $('statusbar').className = 'statusbar' + (tone ? ' tone-' + tone : '');
  $('stGlyph').className = 'sg ' + g;
  $('stLabel').textContent = label;
  $('stModel').textContent = model ? '· ' + shortModel(model) : '';
  $('stText').textContent = text || '';
}

function describeRun() {
  const c = current;
  if (c.source === 'replay') return `replay of ${c.case_id} · ${c.config} · ${c.run_id}`;
  return `${c.config} encounter on ${c.case_id} · ${c.run_id}` + (c.provider ? ` · via ${c.provider}` : '');
}

function setCounters() {
  $('turnNo').textContent = lastTurn === null ? '–' : String(lastTurn);
  $('eventCount').textContent = String(eventCount);
}

/* --- transcript ------------------------------------------------------------------------------- */
function showEmpty() {
  $('flow').replaceChildren(h('div', { class: 'empty' },
    h('span', { class: 'glyphs', 'aria-hidden': 'true' }, KEY.map(([id]) => glyph(id))),
    h('h3', { text: 'Pick a case and start an encounter, or replay a past run.' }),
    h('p', { text: 'The doctor cannot see the diagnosis. Neither can this page, until the encounter is over.' })));
}

function groupFor(turn) {
  if (turns.has(turn)) return turns.get(turn);
  const group = h('section', { class: 'turn-group', 'data-turn': String(turn) });
  let divider = null;
  if (turn === 0) {
    group.id = 'referral';
  } else {
    divider = h('div', { class: 'turn-div', id: 'turn-' + turn }, h('span', { text: 'Turn ' + turn }), h('span', { class: 'rule' }));
    group.append(divider);
  }
  const t = { n: turn, ids: [], alert: false, group };
  turns.set(turn, t);
  const note = $('flow').querySelector('.streaming-note');
  $('flow').insertBefore(group, note);
  observer.observe(group);
  return t;
}

function addEvent(ev) {
  const t = groupFor(ev.turn);
  const p = identify(ev);
  t.ids.push(p.id);
  if (p.id === 'alert') t.alert = true;
  t.group.append(renderMessage(ev));
  eventCount += 1;
  lastTurn = ev.turn;
  setCounters();
  renderRail();
  if (following) scrollToEnd();
}

function scrollToEnd() {
  const t = $('transcript');
  t.scrollTop = t.scrollHeight;
}

function onTranscriptScroll() {
  // Only an upward move is the user leaving the bottom. A scroll event from our
  // own scroll-to-end can land after more messages were appended, when the view
  // is briefly "far from the bottom" without anyone having moved it.
  const t = $('transcript');
  const away = t.scrollHeight - t.scrollTop - t.clientHeight;
  const before = following;
  if (away <= 80) following = true;
  else if (t.scrollTop < lastScrollTop - 1) following = false;
  lastScrollTop = t.scrollTop;
  if (following !== before) updateFollowUi();
}

function updateFollowUi() {
  const hasContent = turns.size > 0;
  const show = hasContent && !following;
  $('contextBar').hidden = !show;
  $('jump').hidden = !show;
  if (!show) return;
  const visible = [...inView].sort((a, b) => a - b);
  const top = visible.find((n) => n > 0) ?? visible[0];
  const at = top === undefined || top === 0 ? 'Referral' : `Turn ${top}` + (current && current.maxTurns ? ` of ${current.maxTurns}` : '');
  $('contextBar').replaceChildren(h('span', { class: 'turnof', text: at }),
    h('span', { class: 'why', text: '· you scrolled up, so the view stopped following the newest message' }));
  $('jumpText').textContent = lastTurn ? `Jump to latest · turn ${lastTurn}` : 'Jump to latest';
}

function renderFinal(final) {
  const abst = !!final.abstain || !final.diagnosis;
  const flags = Array.isArray(final.red_flag) ? final.red_flag : [];
  const ddx = Array.isArray(final.differential) ? final.differential : [];
  const div = h('div', { class: 'final-div', id: 'final-answer' }, h('span', { text: "Doctor's final answer" }), h('span', { class: 'rule' }));
  const card = h('section', { class: 'final', 'aria-label': "Doctor's final answer" },
    h('div', { class: 'col' },
      h('div', { class: 'blk' }, h('span', { class: 'section-h', text: 'Diagnosis' }),
        h('span', { class: 'dx-line' },
          abst ? [h('span', { class: 'dx abstained', text: '(abstained)' }), h('span', { class: 'abst-chip', text: 'abstained' })]
               : [h('span', { class: 'dx', text: final.diagnosis }),
                  h('span', { class: 'conf', text: 'confidence ' + Number(final.confidence).toFixed(2) })])),
      final.rationale ? h('div', { class: 'blk' }, h('span', { class: 'section-h', text: 'Rationale' }),
        h('p', { class: 'rationale', text: final.rationale })) : null,
      h('p', { class: 'flags' }, h('b', { text: 'Red flags' }), ' · ',
        flags.length ? flags.map((f) => `${f.concern}, raised at turn ${f.turn}`).join('; ')
                     : 'none raised during this encounter')),
    h('div', { class: 'col' },
      h('span', { class: 'section-h', text: 'Differential' }),
      ddx.length ? h('ol', { class: 'ddx' }, ddx.map((d, i) => {
        const p = Number(d.probability) || 0;
        return h('li', {},
          h('span', { class: 'ddx-head' }, h('span', { class: 'n', text: String(i + 1) }), h('span', { class: 'name', text: d.diagnosis }),
            h('span', { class: 'p', text: p.toFixed(2) })),
          h('span', { class: 'bar', 'aria-hidden': 'true' }, h('i', { style: `width:${Math.max(2, p * 100)}%` })),
          d.rationale ? h('span', { class: 'why', text: d.rationale }) : null);
      })) : h('span', { class: 'flags', text: 'none recorded' })));
  const flow = $('flow');
  flow.append(div, card);
}

/* --- right column -------------------------------------------------------------------------- */
function jumpTo(id) {
  return (e) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ block: 'start' });
  };
}

function turnLabel(n) { return n === 0 ? 'Referral' : 'Turn ' + n; }

function renderRail() {
  const rail = $('rail');
  const list = [...turns.values()].sort((a, b) => a.n - b.n);
  rail.classList.toggle('ended', !!ended);
  if (!ended) {
    const nodes = [];
    if (list.length) {
      nodes.push(h('nav', { class: 'turns', 'aria-label': 'Turns' }, h('h2', { class: 'rail-h', text: 'Turns' }),
        list.map((t) => {
          const isCurrent = streaming && t.n === lastTurn;
          const href = t.n === 0 ? 'referral' : 'turn-' + t.n;
          return h('a', { class: 'turn-link' + (isCurrent ? ' current' : ''), href: '#' + href, onclick: jumpTo(href) },
            h('span', { class: 'l1' }, h('b', { text: turnLabel(t.n) + (isCurrent && current.source === 'live' ? ' · live' : '') }),
              h('span', { class: 'ev', text: plural(t.ids.length, 'event') })),
            h('span', { class: 'strip', 'aria-hidden': 'true' }, t.ids.map(glyph)));
        })));
    }
    nodes.push(h('div', { class: 'key' }, h('h2', { class: 'rail-h', text: 'Key' }),
      KEY.map(([id, lab, role]) => h('div', { class: 'key-row' }, h('span', { class: 'gbox' }, glyph(id)),
        h('span', { class: 'txt' }, h('span', { class: 'lab c-' + id, text: lab }), h('span', { class: 'role', text: role }))))));
    rail.replaceChildren(...nodes);
    return;
  }

  // Ended: the key goes; a compact index, the final-answer link, and ground truth.
  const final = ended.final;
  const finalLink = final ? h('a', { class: 'final-link', href: '#final-answer', onclick: jumpTo('final-answer'),
    text: final.abstain || !final.diagnosis ? 'Final answer (abstained)' : "Doctor's final answer" }) : null;
  const real = list.filter((t) => t.n > 0).length;
  let nav;
  if (real <= 8) {
    nav = h('nav', { class: 'ended-nav', 'aria-label': 'Turn index' }, h('h2', { class: 'rail-h', text: 'Turns' }),
      h('div', { class: 'chips' }, list.map((t) => {
        const href = t.n === 0 ? 'referral' : 'turn-' + t.n;
        return h('a', { class: 'tchip' + (t.alert ? ' alert' : ''), href: '#' + href, onclick: jumpTo(href), 'data-turn': String(t.n) },
          h('b', { text: turnLabel(t.n) }),
          h('span', { text: t.alert ? `${t.ids.length} · alert` : plural(t.ids.length, 'event') }));
      })),
      finalLink);
  } else {
    nav = h('nav', { class: 'ended-nav', 'aria-label': 'Turn index' },
      h('span', { class: 'navhead' }, h('h2', { class: 'rail-h', text: 'Turns' }), finalLink),
      h('div', { class: 'dense' }, list.map((t) => {
        const href = t.n === 0 ? 'referral' : 'turn-' + t.n;
        return h('a', { class: 'drow' + (t.alert ? ' alert' : ''), href: '#' + href, onclick: jumpTo(href), 'data-turn': String(t.n),
          'aria-label': turnLabel(t.n) + ', ' + plural(t.ids.length, 'event') },
          h('span', { class: 'num', text: t.n === 0 ? 'ref' : String(t.n).padStart(2, '0') }),
          h('span', { class: 'strip', 'aria-hidden': 'true' }, t.ids.map(glyph)),
          h('span', { class: 'ev', text: String(t.ids.length) }));
      })));
  }
  rail.replaceChildren(nav, renderGroundTruth());
  markInView();
}

function markInView() {
  let first = null;
  document.querySelectorAll('#rail .drow').forEach((el) => {
    const on = inView.has(Number(el.dataset.turn));
    el.classList.toggle('inview', on);
    if (on && !first) first = el;
  });
  // A scrolled turn list keeps the highlighted rows in sight.
  const list = first && first.parentElement;
  if (list && (first.offsetTop < list.scrollTop || first.offsetTop + first.offsetHeight > list.scrollTop + list.clientHeight)) {
    list.scrollTop = first.offsetTop - 4;
  }
}

/* --- ground truth: exists only after the status event; fetched only on click ------------ */
function flatten(value) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'object') return String(value);
  if (Array.isArray(value)) return value.map(flatten).join('; ');
  return Object.entries(value).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${flatten(v)}`).join('\n');
}

function renderGroundTruth() {
  const labels = { hidden: 'separate channel', loading: 'separate channel', revealed: 'revealed', refused: 'refused' };
  const leak = gt.state === 'revealed' && gt.data.dx_in_results;
  const top = h('div', { class: 'gt-top' },
    h('span', { class: 'gt-h' }, h('b', { text: 'Ground truth' }), h('span', { text: leak ? 'revealed · leak' : labels[gt.state] })),
    h('p', { class: 'gt-intro', text: 'Held back until the encounter ends, and served on a separate channel that the transcript never touches.' }));
  const panel = h('section', { class: 'gt', 'aria-label': 'Ground truth' }, top);

  if (gt.state === 'hidden' || gt.state === 'loading') {
    panel.append(h('button', { type: 'button', class: 'btn primary', disabled: gt.state === 'loading', onclick: reveal,
      text: gt.state === 'loading' ? 'Revealing…' : 'Reveal the answer' }));
  } else if (gt.state === 'refused') {
    panel.append(h('div', { class: 'reason-box red' }, glyph('alert'), h('span', { text: 'Reveal refused: ' + gt.reason })),
      h('button', { type: 'button', class: 'btn secondary', onclick: reveal, text: 'Try again' }));
  } else {
    const d = gt.data;
    const final = ended.final;
    const answered = !final ? 'no final answer' : (final.abstain || !final.diagnosis ? '(abstained)' : final.diagnosis);
    const mgmt = flatten(d.management_and_follow_up);
    panel.append(...[h('div', { class: 'gt-body' },
      h('div', { class: 'kv' }, h('span', { class: 'k', text: 'Correct diagnosis' }), h('span', { class: 'correct', text: d.correct_diagnosis })),
      h('div', { class: 'kv' }, h('span', { class: 'k', text: 'Doctor answered' }),
        h('span', { class: 'answered' + (final && final.diagnosis && !final.abstain ? '' : ' none'), text: answered }))),
      leak ? h('div', { class: 'reason-box amber', role: 'alert' }, h('span', { class: 'g g-amber-tri', 'aria-hidden': 'true' }),
        h('span', { text: 'The benchmark embeds this diagnosis in the test results the gatekeeper returns, so a correct answer here is not evidence of reasoning.' })) : null,
      h('p', { class: 'note', text: d.note || 'Ground truth from the benchmark. Not a clinical judgement.' }),
      h('p', { class: 'mgmt' }, h('b', { text: 'Management and follow-up' }), ' · ', mgmt || 'none recorded for this case'),
      h('button', { type: 'button', class: 'btn secondary', text: 'Hide the answer', onclick: () => { gt = { state: 'hidden' }; renderRail(); } }),
    ].filter(Boolean));   // native append() would print a null as "null"
  }
  return panel;
}

function reveal() {
  if (!ended || !current) return;          // never before the run has ended
  gt = { state: 'loading' };
  renderRail();
  api(`/api/runs/${encodeURIComponent(current.run_id)}/reveal?case_id=${encodeURIComponent(current.case_id)}`)
    .then((data) => { gt = { state: 'revealed', data }; })
    .catch((e) => { gt = { state: 'refused', reason: e.message }; })
    .finally(renderRail);
}

/* --- streams ------------------------------------------------------------------------------------ */
function resetTranscript() {
  if (observer) observer.disconnect();
  observer = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const n = Number(e.target.dataset.turn);
      if (e.isIntersecting) inView.add(n); else inView.delete(n);
    });
    markInView();
    if (!following) updateFollowUi();
  }, { root: $('transcript') });
  turns = new Map();
  inView = new Set();
  lastTurn = null;
  eventCount = 0;
  ended = null;
  gt = null;
  following = true;
  lastScrollTop = 0;
  $('flow').replaceChildren();
  setCounters();
  updateFollowUi();
}

function openStream(url, info) {
  if (source) source.close();
  resetTranscript();
  current = info;
  streaming = true;
  refusal = null;
  if (info.source === 'live') {
    $('flow').append(h('div', { class: 'streaming-note' }, h('span', { class: 'pad' }),
      h('span', { class: 'txt' }, h('span', { class: 'dots3', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
        'Streaming · the view follows the newest message')));
    setStatus({ glyph: 'run', label: 'Running', model: info.model,
      text: `live ${info.config} encounter on ${info.case_id}` + (info.provider ? ` · via ${info.provider}` : '') });
  } else {
    setStatus({ glyph: 'replay', label: 'Replaying', model: info.model, text: describeRun() });
  }
  updateStartControls();
  renderRunList();
  renderRail();

  source = new EventSource(url);
  source.addEventListener('event', (m) => addEvent(JSON.parse(m.data)));
  source.addEventListener('status', (m) => finish(JSON.parse(m.data)));
  source.onerror = () => {
    if (!source) return;
    source.close();
    source = null;
    streaming = false;
    const note = $('flow').querySelector('.streaming-note');
    if (note) note.remove();
    setStatus({ glyph: 'lost', label: 'Connection lost', model: current.model,
      text: 'the transcript received so far stays on screen' });
    updateStartControls();
    renderRunList();
    renderRail();
  };
}

function finish(s) {
  source.close();
  source = null;
  streaming = false;
  current.model = s.model || current.model;
  const note = $('flow').querySelector('.streaming-note');
  if (note) note.remove();

  if (s.status === 'failed') {
    setStatus({ glyph: 'fail', label: 'Failed', tone: 'fail',
      text: FAILURE[s.error_class] || s.error || 'the run did not complete' });
  } else if (s.status === 'incomplete') {
    setStatus({ glyph: 'inc', label: 'Incomplete', text: 'the run did not complete' });
  } else if (s.stop_reason && s.stop_reason !== 'finalize') {
    setStatus({ glyph: 'cap', label: `Ended (${s.stop_reason})`, model: current.model, text: describeRun(), tone: 'cap' });
  } else {
    setStatus({ glyph: 'done', label: 'Finished', model: current.model, text: describeRun() });
  }

  // Only now may anything to do with ground truth exist.
  ended = s;
  gt = { state: 'hidden' };
  if (s.final) {
    renderFinal(s.final);
  } else {
    $('flow').append(h('div', { class: 'closed' }, h('span', { class: 'rule' }),
      h('span', { class: 'txt', text: 'stream closed · no final answer for this run' }), h('span', { class: 'rule' })));
  }
  updateStartControls();
  renderRail();
  // The rail widens once a run ends, which re-wraps the transcript: scroll after that layout.
  if (following) requestAnimationFrame(scrollToEnd);
  refreshRuns();
  refreshMeta();
}

/* --- wiring ---------------------------------------------------------------------------------- */
function init() {
  showEmpty();
  renderRail();
  setStatus({ glyph: 'idle', label: 'Idle', text: 'nothing loaded' });

  api('/api/cases').then((rows) => {
    const sel = $('case');
    rows.forEach((c) => sel.append(h('option', { value: c.case_id, text: c.case_id, 'data-objective': c.objective })));
    const show = () => { $('objective').textContent = sel.selectedOptions[0] ? sel.selectedOptions[0].dataset.objective : ''; };
    sel.addEventListener('change', () => { show(); refusal = null; updateStartControls(); });
    show();
  });

  document.querySelectorAll('.segmented button').forEach((b) => b.addEventListener('click', () => setConfig(b.dataset.config)));
  $('turns').addEventListener('input', () => { refusal = null; updateStartControls(); });
  $('modelFilter').addEventListener('change', renderRunList);
  $('transcript').addEventListener('scroll', onTranscriptScroll, { passive: true });
  $('jump').addEventListener('click', () => { scrollToEnd(); following = true; updateFollowUi(); });

  $('start').addEventListener('click', () => {
    const body = { case_id: $('case').value, config, max_turns: Number($('turns').value) || 20 };
    $('start').disabled = true;
    api('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then((r) => openStream(`/api/runs/${encodeURIComponent(r.run_id)}/stream`, {
        source: 'live', run_id: r.run_id, case_id: r.case_id, config: r.config,
        model: meta && meta.model, provider: provider(meta && meta.provider_pin), maxTurns: body.max_turns,
      }))
      .catch((e) => {
        refusal = e.message.replace(/^cannot start:\s*/i, '');
        setStatus({ glyph: 'fail', label: 'Refused', tone: 'fail', text: refusal });
        updateStartControls();
      });
  });

  $('replay').addEventListener('click', () => {
    if (!selected) return;
    const row = allRuns.find((r) => r.run_id === selected.run_id && r.case_id === selected.case_id);
    if (!row) return;
    openStream(`/api/runs/${encodeURIComponent(row.run_id)}/stream?case_id=${encodeURIComponent(row.case_id)}`, {
      source: 'replay', run_id: row.run_id, case_id: row.case_id, config: row.config,
      model: row.model, provider: provider(row.provider_pin), maxTurns: row.max_turns,
    });
  });

  refreshMeta().then(refreshRuns);
}

init();
