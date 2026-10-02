import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const ORIGIN = 'http://127.0.0.1:8080';
const ROOM = 's_1a2b3c4d';

const FORM = {
  tenant: 't_9c21a4be', language: 'hi', llm: 'groq/qwen/qwen3.8-27b', channel: 'webrtc', profile: '',
  resolution: '', 'noise-cancellation': '', 'privacy-mode': '', 'agent-mode': '',
  'addressing-mode': '', 'agent-greeting': '', 'recording-layout': '', 'transcription-mode': '', role: 'participant',
  'speech-fillers': false, 'speech-backchannel': true, 'speech-normalization': true,
};

function element(id, form) {
  return {
    id, value: form[id] ?? '', checked: form[id], textContent: '', innerHTML: '', disabled: false, style: {}, children: [],
    appendChild(child) { this.children.push(child); },
  };
}

function page({ search = '', answers, form = FORM }) {
  const elements = new Map();
  const calls = [];
  const run = load('agent-llm.js', 'agent-speech.js', 'agent-addressing.js', 'agent.js', 'client-session.js', 'client-call.js', 'client-captions.js');
  const global = run('globalThis');
  Object.assign(global, {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id, form));
        return elements.get(id);
      },
      createElement: () => element('', form),
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
  assert.equal(calls[0].body.llm, 'groq/qwen/qwen3.8-27b');
  assert.deepEqual(calls[0].body.overrides, {
    agent: { speech: { fillers: { enabled: false }, normalization: 'platform' } },
    turn: { interruption: { backchannel: { enabled: true } } },
  });
  assert.equal(calls[0].body.profile, undefined);
  assert.deepEqual(calls[1].body, { role: 'participant' });
  assert.equal(elements.get('btn-start').disabled, false);
});

test('the Profile field starts empty, so Nivya speaks as the general persona', () => {
  const html = readFileSync(new URL('../testclient.html', import.meta.url), 'utf8');
  assert.match(html, /<input id="profile" value="">/);
});

test('a typed profile is sent with the session', async () => {
  const { run, calls } = page({
    form: { ...FORM, profile: ' support ' },
    answers: { '/sessions': created, [`/sessions/${ROOM}/join`]: joinRefused },
  });
  await run('startCall()');
  assert.equal(calls[0].body.profile, 'support');
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
