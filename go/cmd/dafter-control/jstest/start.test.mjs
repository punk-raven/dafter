import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const ORIGIN = 'http://127.0.0.1:8080';
const ROOM = 's_1a2b3c4d';

const FORM = {
  tenant: 't_9c21a4be', language: 'hi', channel: 'webrtc', profile: 'support',
  resolution: '', 'noise-cancellation': '', 'privacy-mode': '', 'agent-mode': '',
  'addressing-mode': '', 'agent-greeting': '', 'recording-layout': '', role: 'participant',
};

function element(id) {
  return {
    id, value: FORM[id] ?? '', textContent: '', innerHTML: '', disabled: false, style: {}, children: [],
    appendChild(child) { this.children.push(child); },
  };
}

function page({ search = '', answers }) {
  const elements = new Map();
  const calls = [];
  const run = load('agent-addressing.js', 'agent.js', 'client-session.js', 'client-call.js');
  const global = run('globalThis');
  Object.assign(global, {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id));
        return elements.get(id);
      },
      createElement: () => element(''),
    },
    window: { location: { origin: ORIGIN, search, hostname: '127.0.0.1' } },
    URLSearchParams,
    log: () => {},
    showResponse: () => {},
    fillNoiseFilterChoices: () => {},
    async fetch(url, init) {
      calls.push({ url, body: JSON.parse(init.body) });
      const [status, data] = answers[url];
      return { ok: status < 300, json: async () => data };
    },
  });
  return { run, calls, elements };
}

const created = [201, { sessionId: 'sess_7f3a9c21', room: ROOM, configHash: 'a'.repeat(64), config: { agent: {} } }];
const joinRefused = [403, { code: 'forbidden', message: 'refused' }];

test('Start call creates the session and joins it in one click', async () => {
  const { run, calls, elements } = page({ answers: { '/sessions': created, [`/sessions/${ROOM}/join`]: joinRefused } });
  await run('startCall()');
  assert.deepEqual(calls.map((c) => c.url), ['/sessions', `/sessions/${ROOM}/join`]);
  assert.equal(calls[0].body.language, 'hi');
  assert.equal(calls[0].body.overrides, undefined);
  assert.deepEqual(calls[1].body, { role: 'participant' });
  assert.equal(elements.get('btn-start').disabled, false);
});

test('a refused session is never joined', async () => {
  const { run, calls } = page({ answers: { '/sessions': [400, { code: 'invalid_config', message: 'bad' }] } });
  await run('startCall()');
  assert.deepEqual(calls.map((c) => c.url), ['/sessions']);
});

test('the join link names the room and opening it joins straight away', async () => {
  const { run, calls, elements } = page({ search: `?room=${ROOM}`, answers: { [`/sessions/${ROOM}/join`]: joinRefused } });
  assert.equal(run(`joinLink('${ROOM}')`), `${ORIGIN}?room=${ROOM}`);
  run(readFileSync(new URL('../client-media.js', import.meta.url), 'utf8'));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(elements.get('room-id').value, ROOM);
  assert.deepEqual(calls, [{ url: `/sessions/${ROOM}/join`, body: { role: 'participant' } }]);
});
