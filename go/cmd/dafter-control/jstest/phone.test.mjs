import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const NUMBER = '+919876543210';

function page(responses) {
  const run = load('client-phone.js');
  const g = run('globalThis');
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { id, value: '', textContent: '', className: '', title: '', disabled: false, style: {} });
    return elements.get(id);
  };
  const requests = [];
  const logged = [];
  const shown = [];
  g.document = { getElementById: element };
  g.log = (msg, level = 'info') => logged.push({ msg, level });
  g.showResponse = (data) => shown.push(data);
  g.showCreatedSession = (data) => { element('room-id').value = data.room; };
  g.sessionRequest = () => ({ tenantId: 't_9c21a4be', language: 'hi', channel: 'webrtc', profile: 'support' });
  g.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init.body) });
    const [status, data] = responses.shift();
    return { ok: status < 300, status, json: async () => data };
  };
  element('role').value = 'participant';
  return { run, element, requests, logged, shown };
}

const leaks = (p) => [...p.logged.map((l) => l.msg), ...p.shown.map((d) => JSON.stringify(d)), ...['phone-detail', 'phone-problem', 'phone-hint'].map((id) => p.element(id).textContent)]
  .filter((text) => text.includes('9876543210'));

test('an E.164 number passes, written with spaces, dashes or brackets', () => {
  const { run } = page([]);
  const from = run('phoneNumberFrom');
  assert.equal(from(NUMBER), NUMBER);
  assert.equal(from('+91 98765-43210'), NUMBER);
  assert.equal(from('+1 (415) 555.0100'), '+14155550100');
  for (const bad of ['12345', '919876543210', '+0919876543210', '+12345', '+1234567890123456', '+91abc', '', null]) {
    assert.equal(from(bad), null, String(bad));
  }
});

test('a phone call session never carries the meeting\'s phone guests choice', () => {
  const p = page([]);
  p.run('globalThis').sessionRequest = () => ({ tenantId: 't_9c21a4be', language: 'hi', channel: 'webrtc', overrides: { telephony: { phoneGuests: 'dial_out' } } });
  assert.deepEqual(p.run('phoneSessionRequest()'), { tenantId: 't_9c21a4be', language: 'hi', channel: 'telephony' });
});

test('a number that is not E.164 is refused by pointer without a request', async () => {
  const p = page([]);
  p.element('phone-to').value = '12345';
  await p.run('placePhoneCall()');
  assert.equal(p.requests.length, 0);
  assert.equal(p.run('phoneView').state, 'refused');
  assert.equal(p.element('phone-problem').textContent, "Number refused: invalid_config - 1 problem with the request (at '/to': is not an E.164 number, a plus and up to 15 digits)");
  assert.equal(p.logged.at(-1).level, 'error');
  assert.equal(p.element('btn-call').disabled, false);
});

test('Call creates a telephony session on the tenant\'s phone line, then dials it, never logging the number', async () => {
  const p = page([
    [201, { sessionId: 's_7a1c9e20', room: 's_7a1c9e20', configHash: 'a'.repeat(64) }],
    [201, { sessionId: 's_7a1c9e20', participantId: 'p_3d5f7a90', callId: 'SCL_x1' }],
  ]);
  p.element('phone-to').value = '+91 98765 43210';
  await p.run('placePhoneCall()');
  assert.deepEqual(p.requests.map((r) => r.path), ['/sessions', '/sessions/s_7a1c9e20/call/start']);
  assert.deepEqual(p.requests[0].body, { tenantId: 't_9c21a4be', language: 'hi', channel: 'telephony', profile: 'support' });
  assert.deepEqual(p.requests[1].body, { to: NUMBER });
  assert.equal(p.run('phoneView').state, 'placed');
  assert.equal(p.element('phone-state').textContent, 'call placed');
  assert.equal(p.element('phone-detail').textContent, 'session s_7a1c9e20 · caller p_3d5f7a90 · call SCL_x1');
  assert.equal(p.element('room-id').value, 's_7a1c9e20');
  assert.equal(p.element('role').value, 'observer');
  assert.deepEqual(leaks(p), []);
});

test('a call the control plane refuses shows its problems by pointer', async () => {
  const refusal = { code: 'invalid_config', message: '1 problem with the request', retryable: false, details: ["at '/telephony/trunk': names no trunk in the operator's trunk table"] };
  const p = page([
    [201, { sessionId: 's_7a1c9e20', room: 's_7a1c9e20', configHash: 'a'.repeat(64) }],
    [400, refusal],
  ]);
  p.element('phone-to').value = NUMBER;
  await p.run('placePhoneCall()');
  assert.equal(p.run('phoneView').state, 'refused');
  assert.equal(p.element('phone-problem').textContent, "Call refused: invalid_config - 1 problem with the request (at '/telephony/trunk': names no trunk in the operator's trunk table)");
  assert.equal(p.element('role').value, 'participant');
  assert.deepEqual(leaks(p), []);
});

test('a session the control plane refuses stops before dialing', async () => {
  const p = page([[400, { code: 'invalid_config', message: '1 problem with the config', retryable: false, details: ["at '/agent': sealed forbids agent"] }]]);
  p.element('phone-to').value = NUMBER;
  await p.run('placePhoneCall()');
  assert.equal(p.requests.length, 1);
  assert.equal(p.element('phone-problem').textContent, "Session refused: invalid_config - 1 problem with the config (at '/agent': sealed forbids agent)");
});

test('the caller\'s SIP status drives the call state once the page watches the room', () => {
  const p = page([]);
  const phone = p.run('phoneView');
  Object.assign(phone, { sessionId: 's_7a1c9e20', participantId: 'p_3d5f7a90', state: 'placed', room: {} });
  p.run('onPhoneParticipant')({ identity: 'p_3d5f7a90', attributes: { 'sip.callStatus': 'ringing' } });
  assert.equal(phone.state, 'ringing');
  p.run('onPhoneParticipant')({ identity: 'p_3d5f7a90', attributes: { 'sip.callStatus': 'active' } });
  assert.equal(p.element('phone-state').textContent, 'on the line');
  p.run('onPhoneParticipant')({ identity: 'p_other', attributes: { 'sip.callStatus': 'hangup' } });
  assert.equal(phone.state, 'active');
  assert.equal(p.element('btn-call').disabled, true);
  p.run('stopPhone()');
  assert.equal(phone.state, 'placed');
  assert.equal(p.element('btn-call').disabled, false);
});
