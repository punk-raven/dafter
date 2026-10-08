import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const run = load('admin-api.js', 'admin-summary.js');
const catalog = JSON.parse(readFileSync(new URL('../catalog.json', import.meta.url), 'utf8'));
const summary = (kind, doc) => run(`docSummary(${JSON.stringify(kind)}, ${JSON.stringify(doc)})`);

test('a row summary shows values, never field names', () => {
  for (const kind of ['llms', 'profiles', 'tenants', 'languages', 'channels']) {
    for (const [name, doc] of Object.entries(catalog[kind])) {
      const line = summary(kind, doc);
      assert.notEqual(line, '', `${kind} ${name}`);
      assert.doesNotMatch(line, /credentialRef|options|overlay|tuning/, `${kind} ${name}: ${line}`);
      assert.ok(line.split(' · ').length <= 4, `${kind} ${name}: ${line}`);
    }
  }
});

test('an LLM route names its provider, model and key reference', () => {
  assert.equal(summary('llms', catalog.llms['groq/openai/gpt-oss-120b']), 'Groq · openai/gpt-oss-120b · key\u00a0secret://tenants/t_9c21a4be/groq/api-key');
});

test('a profile names its persona or the models it picks and their key', () => {
  assert.equal(summary('profiles', catalog.profiles.support), 'persona support/v3');
  assert.equal(summary('profiles', catalog.profiles['scribe-gemini']),
    'notes by Google gemini-3.5-flash-lite · turns graded by Google gemini-3.6-flash · key\u00a0secret://tenants/t_9c21a4be/gemini/api-key');
});

test('tenants, languages and channels show the values that set them apart', () => {
  assert.equal(summary('tenants', catalog.tenants.t_9c21a4be), 'data stays in ap-south-1 · at most $2.50 a session · phone trunk vobiz');
  assert.equal(summary('languages', catalog.languages['en-IN']),
    'speech-to-text Sarvam saaras:v3-realtime (transcribe) · voice priya (Sarvam bulbul:v3) · end of turn: meaning');
  assert.equal(summary('channels', catalog.channels.telephony), 'video off · audio 8 kHz · agent speaks first');
  assert.equal(summary('defaults', {}), '');
});

test('a row says how long ago it changed and by whom', () => {
  const now = Date.parse('2026-10-03T12:00:00Z');
  assert.equal(run(`relativeTime('2026-10-03T11:59:50Z', ${now})`), 'just now');
  assert.equal(run(`relativeTime('2026-10-03T11:55:00Z', ${now})`), '5 minutes ago');
  assert.equal(run(`relativeTime('2026-10-02T12:00:00Z', ${now})`), '1 day ago');
  assert.equal(run(`relativeTime('not a date', ${now})`), '');
  assert.equal(run(`updatedLine({ updatedAt: '2026-10-03T10:00:00Z', updatedBy: 'priya@ops' }, ${now})`), 'updated 2 hours ago by priya@ops');
  assert.equal(run(`updatedLine({ updatedAt: '2026-10-01T12:00:00Z', updatedBy: 'catalog.json' }, ${now})`), 'updated 2 days ago from catalog.json');
  assert.equal(run(`updatedLine({}, ${now})`), '');
});

test('a read-only LLM route reads as plain rows, options included', () => {
  const rows = JSON.parse(JSON.stringify(run(`glanceRows('llms', ${JSON.stringify(catalog.llms['groq/openai/gpt-oss-120b'])})`)));
  assert.deepEqual(rows, [
    ['Provider', 'Groq'], ['Model', 'openai/gpt-oss-120b'], ['Key', 'secret://tenants/t_9c21a4be/groq/api-key'],
    ['Temperature', '0.4'], ['Max tokens', '800'], ['Reasoning effort', 'low'],
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(run(`glanceRows('defaults', {})`))), []);
});
