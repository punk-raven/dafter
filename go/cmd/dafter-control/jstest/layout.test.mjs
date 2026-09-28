import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

const dir = new URL('../', import.meta.url);
const html = readFileSync(new URL('testclient.html', dir), 'utf8');
const scripts = readdirSync(dir).filter((f) => f.endsWith('.js')).map((f) => readFileSync(new URL(f, dir), 'utf8'));
const advancedAt = html.indexOf('<details id="advanced"');

const MAIN_SCREEN = ['language', 'btn-start', 'btn-mic', 'btn-camera', 'agent-wake', 'btn-leave'];
const ADVANCED = [
  'tenant', 'channel', 'profile', 'privacy-mode', 'resolution', 'noise-cancellation', 'agent-mode',
  'addressing-mode', 'agent-greeting', 'recording-layout', 'btn-create', 'room-id', 'role', 'ice-policy',
  'btn-join', 'my-resolution', 'btn-record-start', 'btn-record-stop', 'agent-toggle', 'stats-panel', 'log',
];

function position(id) {
  const at = html.indexOf(`id="${id}"`);
  assert.notEqual(at, -1, `#${id} is missing`);
  return at;
}

test('Advanced is one section, closed until opened', () => {
  assert.notEqual(advancedAt, -1);
  assert.equal(html.split('<details').length, 2);
  assert.match(html, /<details id="advanced" class="advanced">/);
});

test('the main screen holds only the call, everything else sits under Advanced', () => {
  for (const id of MAIN_SCREEN) assert.ok(position(id) < advancedAt, `#${id} belongs on the main screen`);
  for (const id of ADVANCED) assert.ok(position(id) > advancedAt, `#${id} belongs under Advanced`);
  assert.ok(html.indexOf('onclick="copyJoinLink(this)"') < advancedAt, 'Copy join link belongs on the main screen');
});

test('the language list offers the five focus languages by name, Hindi first chosen', () => {
  const select = html.slice(position('language'), html.indexOf('</select>', position('language')));
  const options = [...select.matchAll(/<option value="([^"]+)"( selected)?>([^<]+)<\/option>/g)];
  assert.deepEqual(options.map((o) => [o[1], o[3]]), [
    ['kn-IN', 'Kannada'], ['hi', 'Hindi'], ['en-IN', 'English'], ['mr-IN', 'Marathi'], ['te-IN', 'Telugu'],
  ]);
  assert.deepEqual(options.filter((o) => o[2]).map((o) => o[1]), ['hi']);
});

test('every element a script looks up by a fixed id exists', () => {
  const declared = new Set([html, ...scripts].flatMap((s) => [...s.matchAll(/id(?:="| = ')([\w-]+)["']/g)].map((m) => m[1])));
  const wanted = new Set(scripts.flatMap((s) => [...s.matchAll(/getElementById\('([\w-]+)'\)/g)].map((m) => m[1])));
  const missing = [...wanted].filter((id) => !declared.has(id));
  assert.deepEqual(missing, []);
});
