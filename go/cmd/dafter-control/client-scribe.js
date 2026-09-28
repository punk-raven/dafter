const SCRIBE_TOPIC = 'dafter.scribe';
const SCRIBE_EVENTS = new Set(['scribe.notes', 'scribe.minutes', 'agent.note_taken', 'agent.turn_scored']);
const MINUTES_WAIT_MS = 20000;
const SCRIBE_REFUSAL_POLL_MS = 3000;
const SCRIBE_REFUSAL_WINDOW_MS = 30000;

const scribeView = {
  enabled: false,
  sessionId: null,
  notes: null,
  notesAt: 0,
  taken: new Map(),
  scores: [],
  failed: 0,
  minutes: null,
  waiter: null,
  refusal: null,
  timers: [],
};

function scribeOverride() {
  return document.getElementById('scribe-mode').value === 'on';
}

function applyScribeOverride(body) {
  const overrides = body.overrides || {};
  overrides.scribe = { enabled: true, consentArtifactId: 'consent_testclient_scribe' };
  const mode = overrides.transcription && overrides.transcription.mode;
  if (mode !== 'live' && mode !== 'both') {
    overrides.transcription = Object.assign(overrides.transcription || {}, {
      mode: mode === 'after_call' ? 'both' : 'live',
      consentArtifactId: 'consent_testclient_transcription',
    });
    log('the scribe reads the live captions, so the override turns live transcription on');
  }
  body.overrides = overrides;
}

function scribePanel() {
  let panel = document.getElementById('scribe-panel');
  if (panel) return panel;
  panel = document.createElement('section');
  panel.id = 'scribe-panel';
  panel.className = 'scribe-panel';
  panel.innerHTML = `
    <div class="scribe-head">
      <strong id="scribe-title">Notes</strong>
      <span id="scribe-meta" class="scribe-meta"></span>
      <span id="scribe-quality" class="scribe-quality"></span>
    </div>
    <div id="scribe-body" class="scribe-body"></div>`;
  document.getElementById('transcripts').append(panel);
  return panel;
}

function scribeSection(parent, title, items) {
  if (!items.length) return;
  const head = document.createElement('div');
  head.className = 'scribe-section';
  head.textContent = title;
  const list = document.createElement('ul');
  list.className = 'scribe-list';
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item;
    list.append(li);
  }
  parent.append(head, list);
}

function actionText(a) {
  const who = [a.owner, a.due].filter(Boolean).join(' · ');
  return who ? `${a.task} - ${who}` : a.task;
}

function scribeQualityText() {
  if (!scribeView.scores.length && !scribeView.failed) return '';
  const mean = scribeView.scores.length
    ? (scribeView.scores.reduce((a, b) => a + b, 0) / scribeView.scores.length).toFixed(2)
    : '-';
  const failed = scribeView.failed ? `, ${scribeView.failed} unscored` : '';
  const n = scribeView.scores.length;
  return `judge ${mean} over ${n} ${n === 1 ? 'reply' : 'replies'}${failed}`;
}

function renderScribe() {
  if (!scribeView.enabled && !scribeView.minutes) return;
  scribePanel();
  const body = document.getElementById('scribe-body');
  const meta = document.getElementById('scribe-meta');
  document.getElementById('scribe-quality').textContent = scribeQualityText();
  body.replaceChildren();
  const m = scribeView.minutes;
  if (m) {
    document.getElementById('scribe-title').textContent = m.final ? 'Minutes' : 'Minutes so far';
    const unpriced = m.unpricedItems ? `, ${m.unpricedItems} unpriced` : '';
    meta.textContent = `cost ₹${m.costInr.toFixed(2)}${unpriced}`;
    renderNotesBody(body, m, m.notes.map((n) => n.text));
    return;
  }
  document.getElementById('scribe-title').textContent = 'Notes';
  const taken = [...scribeView.taken.values()];
  if (scribeView.refusal) {
    const r = scribeView.refusal;
    meta.textContent = '';
    const reason = document.createElement('div');
    reason.className = 'scribe-refusal';
    reason.textContent = `Scribe refused: ${r.code} - ${r.message}${r.details && r.details.length ? ` (${r.details.join('; ')})` : ''}`;
    body.append(reason);
  }
  if (!scribeView.notes) {
    meta.textContent = scribeView.refusal ? '' : 'the scribe writes the first notes about a minute into the call';
    scribeSection(body, 'Notes taken', taken);
    return;
  }
  const age = Math.round((performance.now() - scribeView.notesAt) / 1000);
  meta.textContent = `revision ${scribeView.notes.revision} · ${age}s ago`;
  renderNotesBody(body, scribeView.notes, taken);
}

function renderNotesBody(body, n, taken) {
  const summary = document.createElement('div');
  summary.className = 'scribe-summary';
  summary.textContent = n.summary;
  body.append(summary);
  scribeSection(body, 'Decisions', n.decisions);
  scribeSection(body, 'Action items', n.actionItems.map(actionText));
  scribeSection(body, 'Open questions', n.openQuestions);
  scribeSection(body, 'Notes taken', taken);
}

function noteTakenText() {
  const agent = agentParticipant(agentView.room);
  return `${(agent && agent.name) || 'the agent'} took a note`;
}

function onScribeEvent(event) {
  const p = event.payload;
  if (event.type === 'scribe.notes') {
    if (scribeView.notes && p.revision <= scribeView.notes.revision) return;
    scribeView.notes = p;
    scribeView.notesAt = performance.now();
    for (const n of p.notes) scribeView.taken.set(n.noteId, n.text);
    log(`scribe notes, revision ${p.revision}`);
  } else if (event.type === 'agent.note_taken') {
    scribeView.taken.set(p.noteId, p.text);
    log(noteTakenText());
  } else if (event.type === 'agent.turn_scored') {
    if (typeof p.score === 'number') scribeView.scores.push(p.score);
    else scribeView.failed += 1;
  } else if (event.type === 'scribe.minutes') {
    scribeView.minutes = p;
    log(`${p.final ? 'minutes' : 'minutes so far'} from the scribe, cost ₹${p.costInr.toFixed(2)}`, 'success');
    if (scribeView.waiter) scribeView.waiter();
  }
  renderScribe();
}

function expectScribe(sessionId) {
  const started = Date.now();
  const poll = async () => {
    if (scribeView.sessionId !== sessionId || scribeView.notes) return;
    try {
      const resp = await fetch(`/sessions/${sessionId}`);
      const data = await resp.json();
      if (resp.ok && data.scribeRefusal) {
        scribeView.refusal = data.scribeRefusal;
        log(`Scribe refused: ${data.scribeRefusal.code} - ${data.scribeRefusal.message}`, 'error');
        renderScribe();
        return;
      }
    } catch (err) {
      log(`Could not read the session to check on the scribe: ${err.message}`, 'warn');
      return;
    }
    if (Date.now() - started < SCRIBE_REFUSAL_WINDOW_MS) {
      scribeView.timers.push(setTimeout(poll, SCRIBE_REFUSAL_POLL_MS));
    }
  };
  scribeView.timers.push(setTimeout(poll, SCRIBE_REFUSAL_POLL_MS));
}

function watchScribe(data) {
  stopScribeTimers();
  Object.assign(scribeView, {
    enabled: Boolean(data.config && data.config.scribe && data.config.scribe.enabled),
    sessionId: data.sessionId,
    notes: null,
    taken: new Map(),
    scores: [],
    failed: 0,
    minutes: null,
    waiter: null,
    refusal: null,
  });
  const panel = document.getElementById('scribe-panel');
  if (panel) panel.remove();
  if (!scribeView.enabled) return;
  renderScribe();
  scribeView.timers.push(setInterval(() => { if (scribeView.notes && !scribeView.minutes) renderScribe(); }, 5000));
  expectScribe(data.sessionId);
}

async function requestMinutes(room) {
  if (!scribeView.enabled || !room || scribeView.refusal) return;
  document.getElementById('scribe-meta').textContent = 'writing the minutes...';
  const written = new Promise((resolve) => { scribeView.waiter = resolve; });
  try {
    const body = new TextEncoder().encode(JSON.stringify({ action: 'minutes' }));
    await room.localParticipant.publishData(body, { reliable: true, topic: SCRIBE_TOPIC });
  } catch (err) {
    log(`Could not ask the scribe for minutes: ${err.message}`, 'warn');
    return;
  }
  const timedOut = new Promise((resolve) => setTimeout(() => resolve('timeout'), MINUTES_WAIT_MS));
  if (await Promise.race([written, timedOut]) === 'timeout') {
    log(`No minutes from the scribe after ${MINUTES_WAIT_MS / 1000}s; they are still kept when the call ends`, 'warn');
  }
  scribeView.waiter = null;
}

function stopScribeTimers() {
  for (const t of scribeView.timers) { clearTimeout(t); clearInterval(t); }
  scribeView.timers = [];
}

function stopScribe() {
  stopScribeTimers();
  scribeView.enabled = false;
  scribeView.sessionId = null;
}
