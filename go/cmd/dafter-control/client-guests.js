const GUEST_STATES = { dialing: 'dialing', ringing: 'ringing', active: 'on the line', hangup: 'hung up' };
const GUEST_LINGER_MS = 10000;

const guestView = {
  room: null,
  sessionId: null,
  reason: '',
  busy: false,
  problem: '',
  guests: new Map(),
  timers: [],
};

function guestsBlockedReason(config) {
  if (!config) return 'no session config';
  if (config.privacyMode && config.privacyMode !== 'open') return 'an end-to-end encrypted session takes no phone guests, because the SIP bridge decodes every frame';
  const guests = config.telephony && config.telephony.phoneGuests;
  if (guests === 'dial_in') return 'this session takes phone guests by dial-in only; create it with Phone guests set to dial out or both to call one';
  if (!callsGuestsOut(guests)) return 'this session takes no phone guests; create it with Phone guests set to dial out or both';
  if (!config.telephony.trunk) return 'this tenant has no phone line to call guests in on';
  return '';
}

function isPhoneGuest(participant) {
  if (!participant) return false;
  const kinds = (typeof LivekitClient !== 'undefined' && LivekitClient.ParticipantKind) || {};
  if (kinds.SIP !== undefined && participant.kind === kinds.SIP) return true;
  return PHONE_STATUS_ATTRIBUTE in (participant.attributes || {});
}

function guestFor(identity) {
  let guest = guestView.guests.get(identity);
  if (!guest) {
    guest = { identity, number: guestView.guests.size + 1, state: 'dialing', busy: false };
    guestView.guests.set(identity, guest);
  }
  return guest;
}

function guestTile(guest) {
  const id = `guest-${guest.identity}`;
  let tile = document.getElementById(id);
  if (tile) return tile;
  tile = document.createElement('div');
  tile.id = id;
  tile.className = 'video-tile guest-tile';
  tile.dataset.identity = guest.identity;
  tile.innerHTML = `
    <div class="guest-face"><div class="guest-ring"></div><div class="guest-glyph"><svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path fill="currentColor" d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z"/></svg></div></div>
    <div class="guest-tile-state"><span class="guest-dot"></span><span class="guest-state-text"></span></div>
    <button class="guest-hangup" type="button">Hang up</button>
    <span class="label"></span>`;
  tile.querySelector('.guest-hangup').addEventListener('click', () => hangUpGuest(guest.identity));
  document.getElementById('video-grid').appendChild(tile);
  return tile;
}

function renderGuest(guest) {
  const tile = guestTile(guest);
  tile.dataset.state = guest.state;
  tile.querySelector('.guest-state-text').textContent = GUEST_STATES[guest.state];
  tile.querySelector('.label').textContent = `Phone guest ${guest.number}`;
  const button = tile.querySelector('.guest-hangup');
  button.style.display = guest.state === 'hangup' ? 'none' : '';
  button.disabled = guest.busy;
}

function setGuestState(identity, state) {
  const guest = guestFor(identity);
  if (guest.state === state || guest.state === 'hangup') return;
  guest.state = state;
  log(`Phone guest ${guest.number} ${GUEST_STATES[state]}`, state === 'hangup' ? 'warn' : 'success');
  renderGuest(guest);
  if (state === 'hangup') {
    guestView.timers.push(setTimeout(() => {
      const tile = document.getElementById(`guest-${identity}`);
      if (tile) tile.remove();
    }, GUEST_LINGER_MS));
  }
}

function onGuestParticipant(participant) {
  if (!isPhoneGuest(participant)) return;
  const status = SIP_CALL_STATES[(participant.attributes || {})[PHONE_STATUS_ATTRIBUTE]] || 'dialing';
  const guest = guestFor(participant.identity);
  if (!document.getElementById(`guest-${participant.identity}`)) renderGuest(guest);
  setGuestState(participant.identity, status);
}

function renderGuestControls() {
  const button = document.getElementById('guests-call-btn');
  if (!button) return;
  const input = document.getElementById('guests-to');
  const blocked = guestView.reason !== '';
  input.disabled = blocked;
  button.disabled = blocked || guestView.busy;
  button.title = guestView.reason;
  document.getElementById('guests-problem').textContent = guestView.reason || guestView.problem;
  const live = [...guestView.guests.values()].filter((g) => g.state !== 'hangup').length;
  document.getElementById('guests-count').textContent = live ? `${live} on the call` : '';
}

function guestsSection() {
  let section = document.getElementById('guests-call');
  if (section) return section;
  section = document.createElement('section');
  section.id = 'guests-call';
  section.className = 'guests-call';
  section.innerHTML = `
    <div class="guests-bar">
      <strong>Phone guests</strong>
      <span id="guests-count" class="guests-count"></span>
    </div>
    <div id="guests-dial-in" class="guests-dial-in" style="display:none"></div>
    <div id="guests-dial-out" class="guests-dial">
      <input id="guests-to" type="tel" inputmode="tel" autocomplete="off" spellcheck="false" placeholder="+91XXXXXXXXXX" aria-label="Number to call (E.164)">
      <button id="guests-call-btn" class="guests-btn" type="button">Call</button>
    </div>
    <div id="guests-problem" class="guests-problem"></div>`;
  const panel = agentPanel();
  panel.insertBefore(section, panel.querySelector('.agent-body'));
  document.getElementById('guests-call-btn').addEventListener('click', callGuest);
  document.getElementById('guests-to').addEventListener('keydown', (e) => { if (e.key === 'Enter') callGuest(); });
  return section;
}

function refusalText(during, doc) {
  const details = doc.details && doc.details.length ? ` (${doc.details.join('; ')})` : '';
  return `${during} refused: ${doc.code || 'unknown'} - ${doc.message || 'no message'}${details}`;
}

function refuseGuest(during, doc) {
  guestView.problem = refusalText(during, doc);
  log(guestView.problem, 'error');
  renderGuestControls();
}

async function guestRequest(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { ok: resp.ok, data: await resp.json() };
}

async function callGuest() {
  if (guestView.busy || guestView.reason || !guestView.sessionId) return;
  const input = document.getElementById('guests-to');
  const to = phoneNumberFrom(input.value);
  guestView.problem = '';
  if (!to) {
    refuseGuest('Number', phoneProblem('/to', 'is not an E.164 number, a plus and up to 15 digits'));
    return;
  }
  guestView.busy = true;
  renderGuestControls();
  try {
    const call = await guestRequest(`/sessions/${guestView.sessionId}/call/start`, { to });
    if (!call.ok) {
      refuseGuest('Call', call.data);
      return;
    }
    input.value = '';
    const guest = guestFor(call.data.participantId);
    renderGuest(guest);
    log(`Calling phone guest ${guest.number} as ${guest.identity}${call.data.callId ? ` (call ${call.data.callId})` : ''}`, 'success');
  } catch (err) {
    guestView.problem = `Call failed: ${err.message}`;
    log(guestView.problem, 'error');
  } finally {
    guestView.busy = false;
    renderGuestControls();
  }
}

async function hangUpGuest(identity) {
  const guest = guestView.guests.get(identity);
  if (!guest || guest.busy || guest.state === 'hangup' || !guestView.sessionId) return;
  guest.busy = true;
  renderGuest(guest);
  try {
    const stop = await guestRequest(`/sessions/${guestView.sessionId}/call/${identity}/stop`);
    if (!stop.ok) {
      refuseGuest(`Hang up of phone guest ${guest.number}`, stop.data);
      return;
    }
    setGuestState(identity, 'hangup');
  } catch (err) {
    guestView.problem = `Hang up failed: ${err.message}`;
    log(guestView.problem, 'error');
  } finally {
    guest.busy = false;
    renderGuest(guest);
    renderGuestControls();
  }
}

function watchGuests(room, data) {
  const { RoomEvent } = LivekitClient;
  Object.assign(guestView, { room, sessionId: data.sessionId, reason: guestsBlockedReason(data.config), busy: false, problem: '' });
  guestsSection().style.display = '';
  showDialIn(data.dialIn, data.config && data.config.telephony && data.config.telephony.phoneGuests);
  const refresh = (participant) => { onGuestParticipant(participant); renderGuestControls(); };
  room.on(RoomEvent.ParticipantConnected, refresh);
  room.on(RoomEvent.ParticipantAttributesChanged, (changed, participant) => refresh(participant));
  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    if (!guestView.guests.has(participant.identity)) return;
    setGuestState(participant.identity, 'hangup');
    renderGuestControls();
  });
  room.on(RoomEvent.Connected, () => room.remoteParticipants.forEach(refresh));
  renderGuestControls();
}

function stopGuests() {
  guestView.timers.forEach(clearTimeout);
  Object.assign(guestView, { room: null, sessionId: null, reason: '', busy: false, problem: '', guests: new Map(), timers: [] });
  const section = document.getElementById('guests-call');
  if (!section) return;
  section.style.display = 'none';
  document.getElementById('guests-to').value = '';
  showDialIn(null, null);
}
