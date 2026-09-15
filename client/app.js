/* AgentClinic encounter viewer.
 *
 * The page never asks for ground truth while an encounter is running, and the
 * server would refuse if it did (409). The reveal is a separate fetch on a
 * separate route -- the transcript stream has no field that could carry it.
 */
'use strict';

const $ = (id) => document.getElementById(id);

let source = null;      // the open EventSource, if any
let current = null;     // {run_id, case_id, source: 'live'|'replay'}
let lastTurn = null;
let count = 0;

/* An event's *kind* decides the party, not its actor: the challenger and the
 * cost steward both emit with actor "doctor", because they are sub-roles of the
 * doctor side. Styling by actor alone would render them as the doctor talking
 * to itself. */
const PARTY = {
  objective:      { cls: 'system',     who: 'Referral' },
  question:       { cls: 'doctor',     who: 'Doctor',        tag: 'asks the patient' },
  answer:         { cls: 'patient',    who: 'Patient' },
  exam:           { cls: 'gatekeeper', who: 'Examination' },
  test:           { cls: 'gatekeeper', who: 'Test result' },
  literature:     { cls: 'system',     who: 'Evidence' },
  hypothesis:     { cls: 'doctor',     who: 'Doctor',        tag: 'working differential' },
  challenge:      { cls: 'challenger', who: 'Challenger',    tag: 'argues against the leader' },
  cost_objection: { cls: 'steward',    who: 'Cost steward' },
  red_flag:       { cls: 'alert',      who: 'Red flag' },
  unlisted_test:  { cls: 'gatekeeper', who: 'Gatekeeper',    tag: 'not available for this case' },
  parse_failure:  { cls: 'alert',      who: 'Parse failure' },
  budget:         { cls: 'system',     who: 'Budget' },
  stop:           { cls: 'system',     who: 'Encounter ended' },
};

/* A doctor-side event with actor "doctor" is the doctor *acting*; the same kind
 * from the gatekeeper is the result coming back. */
function partyFor(ev) {
  const base = PARTY[ev.kind] || { cls: 'system', who: ev.kind };
  if ((ev.kind === 'test' || ev.kind === 'exam') && ev.actor === 'doctor') {
    return { cls: 'doctor', who: 'Doctor', tag: ev.kind === 'test' ? 'orders a test' : 'requests an exam' };
  }
  return base;
}

function api(path, opts) {
  return fetch(path, opts).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  });
}

function setStatus(text, state) {
  $('status').textContent = text;
  $('dot').className = 'dot' + (state ? ' ' + state : '');
}

function clearTranscript() {
  $('transcript').innerHTML = '';
  lastTurn = null; count = 0;
  $('eventCount').textContent = '0';
  $('turnNo').textContent = '—';
  $('revealPanel').hidden = true;
  $('revealBody').hidden = true;
  $('revealBody').innerHTML = '';
}

function render(ev) {
  const t = $('transcript');
  if (ev.turn !== lastTurn && ev.kind !== 'objective') {
    const sep = document.createElement('div');
    sep.className = 'turn-sep';
    sep.textContent = 'Turn ' + ev.turn;
    t.appendChild(sep);
    lastTurn = ev.turn;
  }
  const p = partyFor(ev);
  const el = document.createElement('div');
  el.className = 'msg ' + p.cls;

  const who = document.createElement('div');
  who.className = 'who';
  who.appendChild(Object.assign(document.createElement('span'), { textContent: p.who }));

  const bits = [];
  if (p.tag) bits.push(p.tag);
  if (ev.meta && ev.meta.tier) bits.push('matched: ' + ev.meta.tier);
  if (ev.meta && ev.meta.unknown === 'True') bits.push("doesn't know");
  if (bits.length) {
    who.appendChild(Object.assign(document.createElement('span'),
      { className: 'tag', textContent: bits.join(' · ') }));
  }

  el.appendChild(who);
  el.appendChild(Object.assign(document.createElement('div'),
    { className: 'text', textContent: ev.text }));
  t.appendChild(el);

  count += 1;
  $('eventCount').textContent = String(count);
  $('turnNo').textContent = String(ev.turn);
  t.scrollTop = t.scrollHeight;
}

function openStream(url, meta) {
  if (source) source.close();
  clearTranscript();
  current = meta;
  setStatus(meta.source === 'replay' ? 'Replaying…' : 'Running…', 'running');
  $('start').disabled = true;
  $('replay').disabled = true;

  source = new EventSource(url);
  source.addEventListener('event', (m) => render(JSON.parse(m.data)));
  source.addEventListener('status', (m) => {
    const s = JSON.parse(m.data);
    source.close(); source = null;
    $('start').disabled = false;
    $('replay').disabled = false;
    if (s.status === 'failed') {
      setStatus('Failed — ' + (s.error || 'unknown'), 'failed');
      return;
    }
    setStatus(s.stop_reason && s.stop_reason !== 'finalize'
      ? 'Ended (' + s.stop_reason + ')' : 'Finished', 'finished');
    if (s.final) renderFinal(s.final);
    $('revealPanel').hidden = false;
    refreshRuns();
    refreshQuota();
  });
  source.onerror = () => {
    if (!source) return;
    source.close(); source = null;
    setStatus('Connection lost', 'failed');
    $('start').disabled = false;
    $('replay').disabled = false;
  };
}

function renderFinal(final) {
  const t = $('transcript');
  const sep = document.createElement('div');
  sep.className = 'turn-sep';
  sep.textContent = "Doctor's final answer";
  t.appendChild(sep);

  const el = document.createElement('div');
  el.className = 'msg doctor';
  const who = document.createElement('div');
  who.className = 'who';
  who.appendChild(Object.assign(document.createElement('span'), { textContent: 'Diagnosis' }));
  who.appendChild(Object.assign(document.createElement('span'), {
    className: 'tag',
    textContent: 'confidence ' + Number(final.confidence).toFixed(2) +
      (final.abstain ? ' · abstained' : ''),
  }));
  el.appendChild(who);
  el.appendChild(Object.assign(document.createElement('div'), {
    className: 'text',
    textContent: (final.diagnosis || '(abstained)') +
      (final.rationale ? '\n\n' + final.rationale : ''),
  }));
  t.appendChild(el);

  if (Array.isArray(final.differential) && final.differential.length) {
    const d = document.createElement('div');
    d.className = 'msg doctor';
    d.appendChild(Object.assign(document.createElement('div'),
      { className: 'who', textContent: 'Differential' }));
    d.appendChild(Object.assign(document.createElement('div'), {
      className: 'text',
      textContent: final.differential.map((x, i) =>
        `${i + 1}. ${x.diagnosis}  (p=${Number(x.probability).toFixed(2)})`).join('\n'),
    }));
    t.appendChild(d);
  }
  t.scrollTop = t.scrollHeight;
}

/* --- wiring --- */

function refreshQuota() {
  return api('/api/meta').then((m) => {
    $('quota').textContent =
      `${m.model} · ${m.daily_remaining}/${m.daily_limit} requests left today`;
    $('disclaimer').textContent = m.disclaimer;
    updateCostNote(m.daily_remaining);
  }).catch(() => {});
}

function updateCostNote(remaining) {
  const cfg = $('config').value;
  const turns = Number($('turns').value) || 20;
  const projected = turns * (cfg === 'panel' ? 7 : 3) + 3;
  const note = `worst case ~${projected} requests` +
    (remaining !== undefined && projected > remaining ? ' — over today\'s allowance' : '');
  $('costNote').textContent = note;
  $('start').disabled = remaining !== undefined && projected > remaining;
}

function refreshRuns() {
  return api('/api/runs').then((rows) => {
    const sel = $('runs');
    const keep = sel.value;
    sel.innerHTML = '<option value="">—</option>';
    rows.filter((r) => r.source === 'replay' || r.status === 'finished')
        .forEach((r) => {
          const o = document.createElement('option');
          o.value = JSON.stringify({ run_id: r.run_id, case_id: r.case_id });
          o.textContent = `${r.case_id} · ${r.config} · ${r.events} events · ${r.run_id}`;
          sel.appendChild(o);
        });
    sel.value = keep;
    $('replay').disabled = !sel.options.length;
  }).catch(() => {});
}

function init() {
  api('/api/cases').then((rows) => {
    const sel = $('case');
    rows.forEach((c) => {
      const o = document.createElement('option');
      o.value = c.case_id;
      o.textContent = c.case_id;
      o.dataset.objective = c.objective;
      sel.appendChild(o);
    });
    const show = () => { $('objective').textContent = sel.selectedOptions[0].dataset.objective; };
    sel.addEventListener('change', show);
    show();
  });

  $('config').addEventListener('change', refreshQuota);
  $('turns').addEventListener('input', refreshQuota);

  $('start').addEventListener('click', () => {
    api('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        case_id: $('case').value,
        config: $('config').value,
        max_turns: Number($('turns').value) || 20,
      }),
    }).then((r) => {
      openStream(`/api/runs/${r.run_id}/stream`,
        { run_id: r.run_id, case_id: r.case_id, source: 'live' });
    }).catch((e) => setStatus('Refused — ' + e.message, 'failed'));
  });

  $('replay').addEventListener('click', () => {
    const v = $('runs').value;
    if (!v) return;
    const { run_id, case_id } = JSON.parse(v);
    openStream(`/api/runs/${run_id}/stream?case_id=${encodeURIComponent(case_id)}`,
      { run_id, case_id, source: 'replay' });
  });

  $('revealBtn').addEventListener('click', () => {
    if (!current) return;
    api(`/api/runs/${current.run_id}/reveal?case_id=${encodeURIComponent(current.case_id)}`)
      .then((r) => {
        const b = $('revealBody');
        b.hidden = false;
        b.innerHTML = '';
        b.appendChild(Object.assign(document.createElement('div'),
          { className: 'dx', textContent: r.correct_diagnosis }));
        if (r.dx_in_results) {
          b.appendChild(Object.assign(document.createElement('div'), {
            className: 'warn',
            textContent: 'The benchmark embeds this diagnosis in the test results the ' +
              'gatekeeper returns, so a correct answer here is not evidence of reasoning.',
          }));
        }
        b.appendChild(Object.assign(document.createElement('div'),
          { className: 'mg', textContent: r.note }));
        $('revealBtn').disabled = true;
      })
      .catch((e) => setStatus('Reveal refused — ' + e.message, 'failed'));
  });

  refreshQuota();
  refreshRuns();
}

init();
