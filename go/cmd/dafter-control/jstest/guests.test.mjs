import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const NUMBER = '+919876543210';
const SESSION = 's_7a1c9e20';
const MEETING = { privacyMode: 'open', channel: 'webrtc', telephony: { trunk: 'vobiz', phoneGuests: 'dial_out' } };
const SIP = 3;

function element(id) {
  const children = new Map();
  return {
    id, value: '', textContent: '', className: '', title: '', disabled: false, style: {}, dataset: {}, removed: false,
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element(selector));
      return children.get(selector);
    },
    addEventListener() {},
    remove() { this.removed = true; },
  };
}

function page(responses, config = MEETING) {
  const run = load('client-phone.js', 'client-guests.js', 'client-dialin.js');
  const g = run('globalThis');
  const elements = new Map();
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  };
  const requests = [];
  const logged = [];
  g.document = { getElementById: byId };
  g.log = (msg, level = 'info') => logged.push({ msg, level });
  g.setTimeout = () => 0;
  g.clearTimeout = () => {};
  g.LivekitClient = { ParticipantKind: { STANDARD: 0, SIP, AGENT: 4 } };
  g.fetch = async (path, init) => {
    requests.push({ path, body: init.body === undefined ? undefined : JSON.parse(init.body) });
    const [status, data] = responses.shift();
    return { ok: status < 300, status, json: async () => data };
  };
  Object.assign(run('guestView'), { sessionId: SESSION, reason: run('guestsBlockedReason')(config) });
  return { run, byId, requests, logged, view: run('guestView') };
}

const leaks = (p) => [...p.logged.map((l) => l.msg), ...[...p.requests.map((r) => r.path)], p.byId('guests-problem').textContent]
  .filter((text) => text.includes('9876543210'));

test('only an open session that asked for phone guests takes them', () => {
  const { run } = page([]);
  const reason = run('guestsBlockedReason');
  assert.equal(reason(MEETING), '');
  assert.match(reason({ privacyMode: 'open', channel: 'webrtc' }), /takes no phone guests.*dial out/);
  assert.equal(reason({ privacyMode: 'open', channel: 'webrtc', telephony: { trunk: 'vobiz', phoneGuests: 'both' } }), '');
  assert.match(reason({ privacyMode: 'open', channel: 'webrtc', telephony: { trunk: 'vobiz', phoneGuests: 'dial_in' } }), /dial-in only.*dial out or both/);
  assert.match(reason({ privacyMode: 'open', channel: 'webrtc', telephony: { trunk: 'vobiz', phoneGuests: 'off' } }), /takes no phone guests/);
  assert.match(reason({ privacyMode: 'open', channel: 'webrtc', telephony: { phoneGuests: 'dial_out' } }), /no phone line/);
  assert.match(reason({ privacyMode: 'trusted_agent', telephony: { trunk: 'vobiz', phoneGuests: 'dial_out' } }), /end-to-end encrypted/);
  assert.equal(reason(null), 'no session config');
});

test('a session without phone guests shows why and never dials', async () => {
  const p = page([], { privacyMode: 'open', channel: 'webrtc' });
  p.run('renderGuestControls()');
  assert.equal(p.byId('guests-call-btn').disabled, true);
  assert.equal(p.byId('guests-to').disabled, true);
  assert.match(p.byId('guests-problem').textContent, /takes no phone guests/);
  p.byId('guests-to').value = NUMBER;
  await p.run('callGuest()');
  assert.equal(p.requests.length, 0);
});

test('a number that is not E.164 is refused by pointer without a request', async () => {
  const p = page([]);
  p.byId('guests-to').value = '12345';
  await p.run('callGuest()');
  assert.equal(p.requests.length, 0);
  assert.equal(p.byId('guests-problem').textContent, "Number refused: invalid_config - 1 problem with the request (at '/to': is not an E.164 number, a plus and up to 15 digits)");
  assert.equal(p.logged.at(-1).level, 'error');
});

test('each call brings its own numbered phone guest, and the number is never kept or logged', async () => {
  const p = page([
    [201, { sessionId: SESSION, participantId: 'p_3d5f7a90', callId: 'SCL_x1' }],
    [201, { sessionId: SESSION, participantId: 'p_8e1b2c44', callId: 'SCL_x2' }],
  ]);
  for (const typed of ['+91 98765 43210', NUMBER]) {
    p.byId('guests-to').value = typed;
    await p.run('callGuest()');
    assert.equal(p.byId('guests-to').value, '');
  }
  assert.deepEqual(p.requests.map((r) => r.path), [`/sessions/${SESSION}/call/start`, `/sessions/${SESSION}/call/start`]);
  assert.deepEqual(p.requests.map((r) => r.body), [{ to: NUMBER }, { to: NUMBER }]);
  assert.equal(p.byId('guest-p_3d5f7a90').querySelector('.label').textContent, 'Phone guest 1');
  assert.equal(p.byId('guest-p_8e1b2c44').querySelector('.label').textContent, 'Phone guest 2');
  assert.equal(p.byId('guest-p_8e1b2c44').querySelector('.guest-state-text').textContent, 'dialing');
  assert.equal(p.byId('guests-count').textContent, '2 on the call');
  assert.deepEqual(leaks(p), []);
});

test('a call the control plane refuses shows its problems by pointer', async () => {
  const refusal = { code: 'invalid_config', message: '1 problem with the request', details: ["at '/telephony/trunk': names no trunk in the operator's trunk table"] };
  const p = page([[400, refusal]]);
  p.byId('guests-to').value = NUMBER;
  await p.run('callGuest()');
  assert.equal(p.byId('guests-problem').textContent, "Call refused: invalid_config - 1 problem with the request (at '/telephony/trunk': names no trunk in the operator's trunk table)");
  assert.equal(p.view.guests.size, 0);
  assert.deepEqual(leaks(p), []);
});

test('the SIP call status drives each guest tile, and only phones get one', () => {
  const p = page([]);
  const seen = p.run('onGuestParticipant');
  seen({ identity: 'p_9c2e11aa', kind: 0, attributes: {} });
  assert.equal(p.view.guests.size, 0);
  seen({ identity: 'p_3d5f7a90', kind: SIP, attributes: { 'sip.callStatus': 'ringing' } });
  const tile = p.byId('guest-p_3d5f7a90');
  assert.equal(tile.dataset.state, 'ringing');
  seen({ identity: 'p_3d5f7a90', kind: SIP, attributes: { 'sip.callStatus': 'active' } });
  assert.equal(tile.querySelector('.guest-state-text').textContent, 'on the line');
  assert.equal(tile.querySelector('.guest-hangup').style.display, '');
  p.run('setGuestState')('p_3d5f7a90', 'hangup');
  assert.equal(tile.querySelector('.guest-state-text').textContent, 'hung up');
  assert.equal(tile.querySelector('.guest-hangup').style.display, 'none');
  seen({ identity: 'p_3d5f7a90', kind: SIP, attributes: { 'sip.callStatus': 'active' } });
  assert.equal(tile.dataset.state, 'hangup');
});

test('Hang up ends that one phone through the control plane', async () => {
  const p = page([[200, { sessionId: SESSION, participantId: 'p_3d5f7a90' }]]);
  p.run('onGuestParticipant')({ identity: 'p_3d5f7a90', kind: SIP, attributes: { 'sip.callStatus': 'active' } });
  await p.run('hangUpGuest("p_3d5f7a90")');
  assert.deepEqual(p.requests, [{ path: `/sessions/${SESSION}/call/p_3d5f7a90/stop`, body: undefined }]);
  assert.equal(p.view.guests.get('p_3d5f7a90').state, 'hangup');
  await p.run('hangUpGuest("p_3d5f7a90")');
  assert.equal(p.requests.length, 1);
});

test('leaving the meeting forgets the guests and clears the number field', () => {
  const p = page([]);
  p.run('onGuestParticipant')({ identity: 'p_3d5f7a90', kind: SIP, attributes: { 'sip.callStatus': 'active' } });
  p.byId('guests-to').value = '+91 98765';
  p.run('stopGuests()');
  assert.equal(p.view.guests.size, 0);
  assert.equal(p.view.sessionId, null);
  assert.equal(p.byId('guests-to').value, '');
  assert.equal(p.byId('guests-call').style.display, 'none');
});

test('a dial-in meeting shows its number and PIN, and hides the dial row when it calls nobody out', () => {
  const p = page([], { ...MEETING, telephony: { trunk: 'vobiz', phoneGuests: 'dial_in' } });
  p.run('showDialIn')({ numbers: ['+912250001234'], pin: '48151623' }, 'dial_in');
  assert.equal(p.byId('guests-dial-in').textContent, 'Dial in: +912250001234 · PIN 4815 1623');
  assert.equal(p.byId('guests-dial-in').style.display, '');
  assert.equal(p.byId('guests-dial-out').style.display, 'none');
  p.run('showDialIn')({ numbers: ['+912250001234', '+912250005678'], pin: '00420017' }, 'both');
  assert.equal(p.byId('guests-dial-in').textContent, 'Dial in: +912250001234 or +912250005678 · PIN 0042 0017');
  assert.equal(p.byId('guests-dial-out').style.display, '');
  p.run('showDialIn')(undefined, 'dial_out');
  assert.equal(p.byId('guests-dial-in').style.display, 'none');
  p.run('showDialIn')({ numbers: ['+912250001234'], pin: '48151623' }, 'both');
  p.run('stopGuests()');
  assert.equal(p.byId('guests-dial-in').textContent, '');
});
