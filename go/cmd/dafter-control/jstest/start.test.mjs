import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const ORIGIN = 'http://127.0.0.1:8080';
const ROOM = 's_1a2b3c4d';

const FORM = {
  tenant: 't_9c21a4be', language: 'hi', llm: 'groq/qwen/qwen3.8-27b', channel: 'webrtc', profile: '',
  resolution: '', 'noise-cancellation': '', 'privacy-mode': '', 'agent-mode': '',
  'addressing-mode': '', 'agent-greeting': '', 'recording-layout': '', 'transcription-mode': '', 'scribe-mode': '', role: 'participant',
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
  const run = load('agent-llm.js', 'agent-speech.js', 'agent-addressing.js', 'agent.js', 'client-session.js', 'client-call.js', 'client-captions.js', 'client-scribe.js', 'client-dialin.js');
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
      calls.push(init ? { url, body: JSON.parse(init.body) } : { url });
      const [status, data] = answers[url];
      return { ok: status < 300, json: async () => data };
    },
  });
  run(readFileSync(new URL('../client-lobby.js', import.meta.url), 'utf8'));
  return { run, calls, elements };
}

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

const created = [201, { sessionId: 'sess_7f3a9c21', room: ROOM, configHash: 'a'.repeat(64), config: { agent: {} } }];
const RECORDED = { agent: {}, recording: { enabled: true, layout: 'room_composite', consentArtifactId: 'consent_notice_v1' } };
const createdRecorded = [201, { sessionId: 'sess_7f3a9c21', room: ROOM, configHash: 'a'.repeat(64), config: RECORDED }];
const joinRefused = [403, { code: 'forbidden', message: 'refused' }];

test('Start a call creates an unrecorded session and joins it in one click', async () => {
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

test('a recorded session stops at its notice, and joining sends the consent artifact it showed', async () => {
  const { run, calls, elements } = page({ answers: { '/sessions': createdRecorded, [`/sessions/${ROOM}/join`]: joinRefused } });
  await run('startCall()');
  assert.deepEqual(calls.map((c) => c.url), ['/sessions']);
  assert.equal(elements.get('recording-notice').hidden, false);
  assert.equal(elements.get('btn-start').textContent, 'Join call');
  assert.equal(elements.get('lobby-link').value, `${ORIGIN}/?room=${ROOM}`);
  await run('lobbyAction()');
  assert.deepEqual(calls[1], { url: `/sessions/${ROOM}/join`, body: { role: 'participant', recordingConsent: 'consent_notice_v1' } });
});

test('the join link opens that room at its notice and never creates a session', async () => {
  const answers = { [`/sessions/${ROOM}`]: [200, { sessionId: ROOM, room: ROOM, config: RECORDED }], [`/sessions/${ROOM}/join`]: joinRefused };
  const { run, calls, elements } = page({ search: `?room=${ROOM}`, answers });
  assert.equal(run(`joinLink('${ROOM}')`), `${ORIGIN}/?room=${ROOM}`);
  await settled();
  assert.deepEqual(calls, [{ url: `/sessions/${ROOM}` }]);
  assert.equal(elements.get('room-id').value, ROOM);
  assert.equal(elements.get('lobby-title').textContent, 'Join the call');
  assert.equal(elements.get('recording-notice').hidden, false);
  await run('lobbyAction()');
  assert.deepEqual(calls.map((c) => c.url), [`/sessions/${ROOM}`, `/sessions/${ROOM}/join`]);
  assert.equal(calls[1].body.recordingConsent, 'consent_notice_v1');
});

test('an unknown join link says so and offers a new call instead', async () => {
  const answers = { [`/sessions/${ROOM}`]: [400, { code: 'invalid_config', message: 'session not found' }] };
  const { calls, elements } = page({ search: `?room=${ROOM}`, answers });
  await settled();
  assert.deepEqual(calls, [{ url: `/sessions/${ROOM}` }]);
  assert.match(elements.get('lobby-error').textContent, /not valid/);
  assert.equal(elements.get('btn-start').textContent, 'Start a call');
});

test('a refused consent reopens the notice rather than joining', async () => {
  const consentRefused = [400, { code: 'consent_required', message: 'refused' }];
  const answers = { [`/sessions/${ROOM}`]: [200, { sessionId: ROOM, room: ROOM, config: RECORDED }], [`/sessions/${ROOM}/join`]: consentRefused };
  const { run, calls, elements } = page({ answers });
  run(`document.getElementById('room-id').value = '${ROOM}'`);
  assert.equal(await run('joinRoom()'), false);
  assert.deepEqual(calls.map((c) => c.url), [`/sessions/${ROOM}/join`, `/sessions/${ROOM}`]);
  assert.equal(calls[0].body.recordingConsent, undefined);
  assert.equal(elements.get('recording-notice').hidden, false);
});

test('a call without an agent hides the agent panel unless phone guests need it', () => {
  const { run } = page({ answers: {} });
  assert.equal(run("agentQuiet({ agent: { enabled: false } })"), true);
  assert.equal(run("agentQuiet({ agent: { enabled: false }, telephony: { phoneGuests: 'dial_in' } })"), false);
  assert.equal(run("agentQuiet({ agent: { enabled: true } })"), false);
});
