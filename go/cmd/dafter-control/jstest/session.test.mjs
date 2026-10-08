import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

function form(values) {
  const run = load('client-session.js', 'client-dialin.js');
  const g = run('globalThis');
  const fields = { tenant: 't_9c21a4be', language: 'hi', channel: 'webrtc', profile: '', 'recording-layout': '', ...values };
  g.document = { getElementById: (id) => ({ value: fields[id] ?? '', style: {} }) };
  g.log = () => {};
  g.agentOverride = () => null;
  g.addressingOverride = () => null;
  g.transcriptionOverride = () => null;
  g.scribeOverride = () => false;
  g.chosenLlm = () => undefined;
  g.deviceKey = () => 'dv_4b81e0d7a1c2f3e4b5a6c7d8';
  g.speechOverrides = () => {};
  return run;
}

test('a meeting asks for phone guests only when the form says dial out', () => {
  assert.equal(JSON.stringify(form({ 'phone-guests': 'dial_out' })('sessionRequest()').overrides), JSON.stringify({ telephony: { phoneGuests: 'dial_out' } }));
  assert.equal(form({ 'phone-guests': '' })('sessionRequest()').overrides, undefined);
});

test('a dial-in meeting states its caller check, a dial-out one states none', () => {
  const telephony = (values) => form(values)('sessionRequest()').overrides.telephony;
  assert.deepEqual(JSON.parse(JSON.stringify(telephony({ 'phone-guests': 'dial_in', 'dial-in-check': 'pin' }))), { phoneGuests: 'dial_in', dialIn: { callerCheck: 'pin' } });
  assert.deepEqual(JSON.parse(JSON.stringify(telephony({ 'phone-guests': 'both', 'dial-in-check': 'number' }))), { phoneGuests: 'both', dialIn: { callerCheck: 'number' } });
  assert.deepEqual(JSON.parse(JSON.stringify(telephony({ 'phone-guests': 'dial_out', 'dial-in-check': 'number' }))), { phoneGuests: 'dial_out' });
});

test('a telephony session never asks for phone guests, because it is the call itself', () => {
  const body = form({ channel: 'telephony', 'phone-guests': 'dial_out' })('sessionRequest()');
  assert.equal(body.channel, 'telephony');
  assert.equal(body.overrides, undefined);
});

test('a session names the agent the form names, and leaves it to the defaults otherwise', () => {
  assert.equal(form({ 'agent-name': ' maya ' })('sessionRequest()').agent, 'maya');
  assert.equal(form({})('sessionRequest()').agent, undefined);
});
