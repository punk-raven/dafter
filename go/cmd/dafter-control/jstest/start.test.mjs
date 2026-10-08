import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const ORIGIN = 'http://127.0.0.1:8080';
const ROOM = 's_1a2b3c4d';
const DEVICE = 'dv_4b81e0d7a1c2f3e4b5a6c7d8';

const FORM = {
  tenant: 't_9c21a4be', language: 'hi', llm: 'groq/qwen/qwen3.8-27b', channel: 'webrtc', profile: '',
  resolution: '', 'noise-cancellation': '', 'privacy-mode': '', 'agent-mode': '',
  'agent-on': true, 'phone-guests': '', 'dial-in-check': 'pin',
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
  const urls = [];
  const run = load('client-names.js', 'agent-llm.js', 'agent-speech.js', 'agent-addressing.js', 'agent.js', 'client-session.js', 'client-call.js', 'client-captions.js', 'client-scribe.js', 'client-dialin.js');
  const global = run('globalThis');
  Object.assign(global, {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id, form));
        return elements.get(id);
      },
      createElement: () => element('', form),
    },
    window: { location: { origin: ORIGIN, search, hostname: '127.0.0.1' }, history: { replaceState: (state, title, url) => urls.push(url) } },
    URLSearchParams,
    localStorage: { getItem: (key) => (key === 'dafter.device' ? DEVICE : null), setItem() {} },
    log: () => {},
    showResponse: () => {},
    fillNoiseFilterChoices: () => {},
    watchTileLayout: () => {},
    async fetch(url, init) {
      calls.push(init ? { url, body: JSON.parse(init.body) } : { url });
      const [status, data] = answers[url];
      return { ok: status < 300, json: async () => data };
    },
  });
  run(readFileSync(new URL('../client-lobby.js', import.meta.url), 'utf8'));
  return { run, calls, elements, urls };
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
  assert.equal(calls[0].body.device, DEVICE);
  assert.deepEqual(calls[0].body.overrides, {
    agent: { enabled: true, speech: { fillers: { enabled: false }, normalization: 'platform' } },
    turn: { interruption: { backchannel: { enabled: true } } },
  });
  assert.equal(calls[0].body.profile, undefined);
  assert.deepEqual(calls[1].body, { role: 'participant', device: DEVICE });
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
  assert.deepEqual(calls[1], { url: `/sessions/${ROOM}/join`, body: { role: 'participant', device: DEVICE, recordingConsent: 'consent_notice_v1' } });
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

test('a join link shows the call\'s own language and what is kept, and hides the choices only its starter makes', async () => {
  const config = { ...RECORDED, language: 'kn-IN', transcription: { mode: 'live', consentArtifactId: 'consent_transcription_v1' } };
  const answers = { [`/sessions/${ROOM}`]: [200, { sessionId: ROOM, room: ROOM, config }] };
  const { elements } = page({ search: `?room=${ROOM}`, answers });
  await settled();
  assert.equal(elements.get('language').value, 'kn-IN');
  assert.equal(elements.get('language').disabled, true);
  assert.equal(elements.get('lobby-create').hidden, true);
  assert.equal(elements.get('notice-what').textContent, 'This call is recorded and transcribed.');
  assert.match(elements.get('notice-detail').textContent, /each person on their own/);
});

test('a new call offers its language and choices, and an unrecorded one shows no notice', () => {
  const { elements } = page({ answers: {} });
  assert.equal(elements.get('language').disabled, false);
  assert.equal(elements.get('lobby-create').hidden, false);
  assert.equal(elements.get('recording-notice').hidden, true);
});

test('a call started without the agent asks for no transcription, because only the agent transcribes', async () => {
  const { run, calls } = page({ form: { ...FORM, 'agent-on': false }, answers: { '/sessions': [400, { code: 'invalid_config', message: 'bad' }] } });
  await run('startCall()');
  assert.equal(calls[0].body.overrides.agent.enabled, false);
  assert.deepEqual(calls[0].body.overrides.transcription, { mode: 'off' });
});

test('a link to a call that has ended says so instead of offering to join it', async () => {
  const answers = { [`/sessions/${ROOM}`]: [200, { sessionId: ROOM, room: ROOM, config: RECORDED, endedAt: '2026-10-06T20:00:00Z' }] };
  const { calls, elements } = page({ search: `?room=${ROOM}`, answers });
  await settled();
  assert.deepEqual(calls, [{ url: `/sessions/${ROOM}` }]);
  assert.match(elements.get('lobby-error').textContent, /has ended/);
  assert.equal(elements.get('btn-start').textContent, 'Start a call');
  assert.equal(elements.get('recording-notice').hidden, true);
});

test('a join refused because the call ended says so', async () => {
  const ended = [410, { code: 'session_ended', message: 'refused' }];
  const { run, elements } = page({ answers: { [`/sessions/${ROOM}/join`]: ended } });
  run(`document.getElementById('room-id').value = '${ROOM}'`);
  assert.equal(await run('joinRoom()'), false);
  assert.match(elements.get('lobby-error').textContent, /has ended/);
});

test('a call link cannot start a new call while it is still being opened', async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { run, elements } = page({ search: `?room=${ROOM}`, answers: {} });
  run('globalThis').fetch = async () => { await pending; return { ok: true, json: async () => ({ sessionId: ROOM, room: ROOM, config: RECORDED }) }; };
  run(`openRoomLobby('${ROOM}')`);
  assert.equal(elements.get('btn-start').disabled, true);
  release();
  await settled();
  await settled();
  assert.equal(elements.get('btn-start').disabled, false);
  assert.equal(elements.get('btn-start').textContent, 'Join call');
});

const LIVE = [200, { sessionId: ROOM, room: ROOM, config: { ...RECORDED, language: 'kn-IN' } }];

test('leaving a call that is still going offers to rejoin it or start a new one', async () => {
  const { run, calls, elements } = page({ answers: { [`/sessions/${ROOM}`]: LIVE } });
  Object.assign(run('globalThis'), { room: null, cleanup: () => {} });
  run(`lastRoomId = '${ROOM}'`);
  await run('leaveSession()');
  assert.deepEqual(calls.map((c) => c.url), [`/sessions/${ROOM}`]);
  assert.equal(elements.get('lobby-title').textContent, 'You left the call');
  assert.equal(elements.get('lobby-left').hidden, false);
  assert.equal(elements.get('lobby-identity').hidden, true);
  assert.equal(elements.get('lobby-share').hidden, true);
  assert.equal(elements.get('btn-start').textContent, 'Rejoin');
  assert.equal(elements.get('btn-new-call').hidden, false);
  assert.equal(elements.get('recording-notice').hidden, false, 'a rejoin is a join, so the notice it consents to is shown again');
});

test('a new call after leaving clears the old call from the address and offers every start option again', async () => {
  const { run, elements, urls } = page({ answers: { [`/sessions/${ROOM}`]: LIVE } });
  Object.assign(run('globalThis'), { room: null, cleanup: () => {} });
  run(`lastRoomId = '${ROOM}'`);
  await run('leaveSession()');
  run('startNewCall()');
  assert.equal(urls.at(-1), '/');
  assert.equal(run('lastRoomId'), null);
  assert.equal(elements.get('lobby-title').textContent, 'Start a call');
  assert.equal(elements.get('language').disabled, false);
  assert.equal(elements.get('lobby-identity').hidden, false);
  assert.equal(elements.get('lobby-create').hidden, false);
  assert.equal(elements.get('lobby-left').hidden, true);
  assert.equal(elements.get('btn-new-call').hidden, true);
  assert.equal(elements.get('btn-start').textContent, 'Start a call');
  assert.equal(elements.get('recording-notice').hidden, true);
});

test('leaving a call that has since ended says so and offers only a new call', async () => {
  const ended = [200, { sessionId: ROOM, room: ROOM, config: RECORDED, endedAt: '2026-10-07T07:00:00Z' }];
  const { run, elements, urls } = page({ answers: { [`/sessions/${ROOM}`]: ended } });
  Object.assign(run('globalThis'), { room: null, cleanup: () => {} });
  run(`lastRoomId = '${ROOM}'`);
  await run('leaveSession()');
  assert.match(elements.get('lobby-error').textContent, /has ended/);
  assert.equal(elements.get('btn-start').textContent, 'Start a call');
  assert.equal(elements.get('btn-new-call').hidden, true);
  assert.equal(elements.get('language').disabled, false);
  assert.equal(urls.at(-1), '/');
});

test('a join link also offers to start a new call instead', async () => {
  const { elements } = page({ search: `?room=${ROOM}`, answers: { [`/sessions/${ROOM}`]: LIVE } });
  await settled();
  assert.equal(elements.get('lobby-title').textContent, 'Join the call');
  assert.equal(elements.get('btn-new-call').hidden, false);
  assert.equal(elements.get('lobby-identity').hidden, false);
});
