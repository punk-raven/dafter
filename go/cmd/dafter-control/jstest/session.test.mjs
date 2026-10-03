import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

function form(values) {
  const run = load('client-session.js');
  const g = run('globalThis');
  const fields = { tenant: 't_9c21a4be', language: 'hi', channel: 'webrtc', profile: '', 'recording-layout': '', ...values };
  g.document = { getElementById: (id) => ({ value: fields[id] ?? '' }) };
  g.log = () => {};
  g.agentOverride = () => null;
  g.addressingOverride = () => null;
  g.transcriptionOverride = () => null;
  g.scribeOverride = () => false;
  g.chosenLlm = () => undefined;
  g.speechOverrides = () => {};
  return run;
}

test('a meeting asks for phone guests only when the form says dial out', () => {
  assert.equal(JSON.stringify(form({ 'phone-guests': 'dial_out' })('sessionRequest()').overrides), JSON.stringify({ telephony: { phoneGuests: 'dial_out' } }));
  assert.equal(form({ 'phone-guests': '' })('sessionRequest()').overrides, undefined);
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
