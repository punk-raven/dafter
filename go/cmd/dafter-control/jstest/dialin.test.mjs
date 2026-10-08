import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const SESSION = 's_7a1c9e20';

function page(fields, responses = []) {
  const run = load('client-phone.js', 'client-dialin.js');
  const g = run('globalThis');
  const elements = new Map();
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, { id, value: fields[id] ?? '', textContent: '', style: {} });
    return elements.get(id);
  };
  const requests = [];
  const logged = [];
  g.document = { getElementById: byId };
  g.log = (msg, level = 'info') => logged.push({ msg, level });
  g.fetch = async (path, init) => {
    requests.push({ path, method: init.method, body: JSON.parse(init.body) });
    const [status, data] = responses.shift();
    return { ok: status < 300, status, json: async () => data };
  };
  return { run, byId, requests, logged };
}

const created = (phoneGuests, callerCheck) => ({ sessionId: SESSION, config: { telephony: { trunk: 'vobiz', phoneGuests, dialIn: { callerCheck } } } });

test('the caller check and allowed numbers appear only when the meeting takes dial-in and reads numbers', () => {
  for (const [guests, check, fields, numbers] of [
    ['', 'pin', 'none', 'none'], ['dial_out', 'number', 'none', 'none'], ['dial_in', 'pin', '', 'none'],
    ['dial_in', 'pin_and_number', '', ''], ['both', 'number', '', ''],
  ]) {
    const p = page({ 'phone-guests': guests, 'dial-in-check': check });
    p.run('renderDialInFields()');
    assert.equal(p.byId('dial-in-fields').style.display, fields, `${guests} ${check}`);
    assert.equal(p.byId('dial-in-numbers-field').style.display, numbers, `${guests} ${check}`);
  }
});

test('allowed numbers go to their own route once the session exists, and are never logged', async () => {
  const p = page({ 'dial-in-numbers': '+91 98765 43210, +919876500000' }, [[200, { sessionId: SESSION, allowedNumbers: 2 }]]);
  await p.run('allowDialInNumbers')(created('dial_in', 'pin_and_number'));
  assert.deepEqual(p.requests, [{ path: `/sessions/${SESSION}/dial-in/numbers`, method: 'PUT', body: { numbers: ['+919876543210', '+919876500000'] } }]);
  assert.equal(p.byId('dial-in-numbers').value, '');
  assert.equal(p.logged.at(-1).level, 'success');
  assert.ok(p.logged.every((l) => !l.msg.includes('98765')));
});

test('a malformed allowed number sends nothing and names the entry, not the number', async () => {
  const p = page({ 'dial-in-numbers': '+919876543210, 98765' });
  await p.run('allowDialInNumbers')(created('both', 'number'));
  assert.equal(p.requests.length, 0);
  assert.match(p.logged.at(-1).msg, /entries 2 are not E\.164/);
  assert.ok(p.logged.every((l) => !l.msg.includes('98765')));
});

test('a PIN-only or dial-out session sends no numbers', async () => {
  for (const data of [created('dial_in', 'pin'), created('dial_out', 'number'), { sessionId: SESSION, config: {} }]) {
    const p = page({ 'dial-in-numbers': '+919876543210' });
    await p.run('allowDialInNumbers')(data);
    assert.equal(p.requests.length, 0);
  }
});
