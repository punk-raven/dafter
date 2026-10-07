import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

function call(myName) {
  const run = load('client-tiles.js', 'client-names.js');
  const g = run('globalThis');
  const handlers = new Map();
  const sent = [];
  g.RoomEvent = { DataReceived: 'data', ParticipantConnected: 'connected' };
  g.TextEncoder = TextEncoder;
  g.TextDecoder = TextDecoder;
  g.log = () => {};
  g.myName = () => myName;
  const caption = { dataset: { speaker: 'p_9c2e11aa' }, textContent: 'Guest' };
  g.document = { getElementById: () => null, querySelectorAll: () => [caption] };
  const target = {
    on(event, handler) { handlers.set(event, handler); },
    localParticipant: {
      identity: 'p_4b81e0d7',
      publishData(payload, options) {
        sent.push(JSON.parse(JSON.stringify({ message: JSON.parse(new TextDecoder().decode(payload)), to: options.destinationIdentities })));
        return Promise.resolve();
      },
    },
  };
  run('watchNames')(target);
  const receive = (identity, message) => handlers.get('data')(new TextEncoder().encode(JSON.stringify(message)), { identity }, 0, 'dafter.name');
  return { run, handlers, sent, receive, caption, target };
}

test('whoever was in the call asks a newcomer its name, so a name sent before it was known is never lost', () => {
  const host = call('Asha');
  host.handlers.get('connected')({ identity: 'p_9c2e11aa' });
  assert.deepEqual(host.sent, [{ message: { name: 'Asha', ask: true }, to: ['p_9c2e11aa'] }]);

  const joiner = call('Ravi');
  joiner.receive('p_4b81e0d7', { name: 'Asha', ask: true });
  assert.equal(joiner.run('nameOf')('p_4b81e0d7', null), 'Asha');
  assert.deepEqual(joiner.sent, [{ message: { name: 'Ravi' }, to: ['p_4b81e0d7'] }]);

  host.receive('p_9c2e11aa', { name: 'Ravi' });
  assert.equal(host.run('nameOf')('p_9c2e11aa', null), 'Ravi');
  assert.equal(host.caption.textContent, 'Ravi', 'a line captioned before the name arrived keeps saying Guest');
  assert.equal(host.sent.length, 1);
});

test('a name is cleaned of control characters and capped', () => {
  const host = call('Asha');
  host.receive('p_9c2e11aa', { name: `  Ra\u0007vi${'x'.repeat(60)}` });
  assert.equal(host.run('nameOf')('p_9c2e11aa', null), `Ravi${'x'.repeat(36)}`);
});

test('a joiner asks everyone already there for their names, so rejoining a call nobody saw it leave still names them', () => {
  const joiner = call('Ravi');
  joiner.run('nameJoined')(joiner.target);
  assert.deepEqual(joiner.sent, [{ message: { name: 'Ravi', ask: true } }]);

  const host = call('Asha');
  host.receive('p_9c2e11aa', { name: 'Ravi', ask: true });
  assert.equal(host.run('nameOf')('p_9c2e11aa', null), 'Ravi');
  assert.deepEqual(host.sent, [{ message: { name: 'Asha' }, to: ['p_9c2e11aa'] }]);
});
