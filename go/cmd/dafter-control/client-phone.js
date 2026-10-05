const PHONE_NUMBER = /^\+[1-9][0-9]{6,14}$/;
const PHONE_SEPARATORS = /[\s().-]/g;
const PHONE_CHANNEL = 'telephony';
const PHONE_STATUS_ATTRIBUTE = 'sip.callStatus';

const PHONE_STATES = {
  idle: 'no call',
  creating: 'creating session',
  dialing: 'dialing',
  placed: 'call placed',
  ringing: 'ringing',
  active: 'on the line',
  hangup: 'hung up',
  refused: 'refused',
  failed: 'failed',
};

const PHONE_LIVE_STATES = new Set(['dialing', 'ringing', 'active']);

const SIP_CALL_STATES = {
  dialing: 'dialing',
  ringing: 'ringing',
  active: 'active',
  automation: 'active',
  hangup: 'hangup',
};

const phoneView = {
  busy: false,
  state: 'idle',
  sessionId: null,
  participantId: null,
  callId: null,
  problem: null,
  room: null,
};

function phoneNumberFrom(raw) {
  const number = String(raw || '').replace(PHONE_SEPARATORS, '');
  return PHONE_NUMBER.test(number) ? number : null;
}

function phoneProblem(pointer, because) {
  return { code: 'invalid_config', message: '1 problem with the request', retryable: false, details: [`at '${pointer}': ${because}`] };
}

function phoneProblemText() {
  const p = phoneView.problem;
  if (!p) return '';
  const doc = p.doc || {};
  const details = doc.details && doc.details.length ? ` (${doc.details.join('; ')})` : '';
  return `${p.during} refused: ${doc.code || 'unknown'} - ${doc.message || 'no message'}${details}`;
}

function phoneDetail() {
  if (!phoneView.sessionId) return '';
  const parts = [`session ${phoneView.sessionId}`];
  if (phoneView.participantId) parts.push(`caller ${phoneView.participantId}`);
  if (phoneView.callId) parts.push(`call ${phoneView.callId}`);
  return parts.join(' · ');
}

function phoneHint() {
  if (phoneView.room) return '';
  if (phoneView.state === 'placed') return 'Join Room below (observer) to watch the agent, transcript and turn latency.';
  return '';
}

function renderPhone() {
  const pill = document.getElementById('phone-state');
  if (!pill) return;
  pill.textContent = PHONE_STATES[phoneView.state];
  pill.className = `phone-pill phone-pill-${phoneView.state}`;
  document.getElementById('phone-detail').textContent = phoneDetail();
  document.getElementById('phone-hint').textContent = phoneHint();
  document.getElementById('phone-problem').textContent = phoneProblemText();
  document.getElementById('phone-divider').style.display = phoneView.room ? 'none' : '';
  const button = document.getElementById('btn-call');
  button.disabled = phoneView.busy || phoneView.room !== null;
  button.title = phoneView.room ? 'leave the room before placing another call' : '';
}

function setPhoneState(state) {
  phoneView.state = state;
  renderPhone();
}

function refusePhone(during, doc) {
  phoneView.problem = { during, doc };
  log(phoneProblemText(), 'error');
  setPhoneState('refused');
}

function phoneSessionRequest() {
  const body = Object.assign(sessionRequest(), { channel: PHONE_CHANNEL });
  if (body.overrides) delete body.overrides.telephony;
  if (body.overrides && !Object.keys(body.overrides).length) delete body.overrides;
  return body;
}

async function postPhoneJSON(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  showResponse(data);
  return { ok: resp.ok, data };
}

async function placePhoneCall() {
  if (phoneView.busy || phoneView.room) return;
  Object.assign(phoneView, { sessionId: null, participantId: null, callId: null, problem: null });
  const to = phoneNumberFrom(document.getElementById('phone-to').value);
  if (!to) {
    refusePhone('Number', phoneProblem('/to', 'is not an E.164 number, a plus and up to 15 digits'));
    return;
  }
  phoneView.busy = true;
  setPhoneState('creating');
  try {
    log(`Creating a ${PHONE_CHANNEL} session for a phone call...`);
    const session = await postPhoneJSON('/sessions', phoneSessionRequest());
    if (!session.ok) {
      refusePhone('Session', session.data);
      return;
    }
    showCreatedSession(session.data);
    phoneView.sessionId = session.data.sessionId;
    setPhoneState('dialing');
    log(`Dialing out from session ${phoneView.sessionId} via POST /sessions/{id}/call/start...`);
    const call = await postPhoneJSON(`/sessions/${phoneView.sessionId}/call/start`, { to });
    if (!call.ok) {
      refusePhone('Call', call.data);
      return;
    }
    phoneView.participantId = call.data.participantId || null;
    phoneView.callId = call.data.callId || null;
    document.getElementById('role').value = 'observer';
    log(`Phone call placed as ${phoneView.participantId}${phoneView.callId ? ` (call ${phoneView.callId})` : ''}; join as observer to watch`, 'success');
    setPhoneState('placed');
  } catch (err) {
    log(`Phone call failed: ${err.message}`, 'error');
    setPhoneState('failed');
  } finally {
    phoneView.busy = false;
    renderPhone();
  }
}

function onPhoneParticipant(participant) {
  if (!participant || participant.identity !== phoneView.participantId) return;
  const status = SIP_CALL_STATES[(participant.attributes || {})[PHONE_STATUS_ATTRIBUTE]];
  if (status && status !== phoneView.state) {
    log(`Phone call ${PHONE_STATES[status]}`, status === 'hangup' ? 'warn' : 'success');
    setPhoneState(status);
  }
}

function watchPhone(room, data) {
  phoneView.room = room;
  renderPhone();
  if (!phoneView.participantId || data.sessionId !== phoneView.sessionId) return;
  const { RoomEvent } = LivekitClient;
  room.on(RoomEvent.ParticipantConnected, onPhoneParticipant);
  room.on(RoomEvent.ParticipantAttributesChanged, (changed, participant) => onPhoneParticipant(participant));
  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    if (participant.identity !== phoneView.participantId || phoneView.state === 'hangup') return;
    log('Phone call ended: the caller left the room', 'warn');
    setPhoneState('hangup');
  });
  room.on(RoomEvent.Connected, () => onPhoneParticipant(room.remoteParticipants.get(phoneView.participantId)));
}

function stopPhone() {
  phoneView.room = null;
  if (PHONE_LIVE_STATES.has(phoneView.state)) phoneView.state = 'placed';
  renderPhone();
}
