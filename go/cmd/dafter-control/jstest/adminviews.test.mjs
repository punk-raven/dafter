import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const run = load('admin-api.js', 'admin-fields.js', 'admin-diff.js', 'admin-agent.js', 'admin-docs.js', 'admin-publish.js', 'admin-releases.js', 'admin.js');
const plain = (value) => JSON.parse(JSON.stringify(value));

test('the hash routes to a view, a kind, or one document', () => {
  assert.deepEqual(plain(run(`parseRoute('')`)), { view: 'agents', name: null });
  assert.deepEqual(plain(run(`parseRoute('#/releases')`)), { view: 'releases', name: null });
  assert.deepEqual(plain(run(`parseRoute('#/agents/new')`)), { view: 'agents', name: 'new' });
  assert.deepEqual(plain(run(`parseRoute('#/llms/google%2Fgemma')`)), { view: 'llms', name: 'google/gemma' });
  assert.deepEqual(plain(run(`parseRoute('#/nowhere/x')`)), { view: 'agents', name: null });
});

test('an agent form drops blank words and leaves out empty lists', () => {
  assert.deepEqual(plain(run(`agentDocumentFrom({ name: ' Maya ', aliases: ['', ' '], nearMisses: [' Maia ', '', 'Mira'], profile: '' })`)),
    { name: 'Maya', nearMisses: ['Maia', 'Mira'] });
  assert.deepEqual(plain(run(`agentValuesOf({ name: 'Maya', profile: 'support' })`)),
    { name: 'Maya', aliases: [], nearMisses: [], profile: 'support' });
});

test('a document says whether what is stored is what is live', () => {
  assert.equal(run(`docStatus({ published: true, liveRevision: 4 }).label`), 'Live');
  assert.equal(run(`docStatus({ published: false, liveRevision: 4 }).label`), 'Unpublished changes');
  assert.equal(run(`docStatus({ published: false }).label`), 'Not published');
  assert.equal(run(`docSummary('agents', { name: 'Maya', aliases: ['Mya'], nearMisses: ['Maia', 'Mira'], profile: 'support' })`),
    'profile support · 1 alias · 2 near misses');
});

test('the raw editor accepts only a JSON object', () => {
  assert.deepEqual(plain(run(`parseJsonDocument('{"name":"Maya"}')`)), { doc: { name: 'Maya' }, error: null });
  assert.match(run(`parseJsonDocument('{"name":').error`), /^Not valid JSON/);
  assert.equal(run(`parseJsonDocument('[1]').error`), 'The document must be a JSON object.');
});

test('a preview asks for the draft unless the live release is chosen, naming only what was picked', () => {
  assert.deepEqual(plain(run(`previewBody({ source: 'draft', tenantId: 't_9c21a4be', agent: 'maya', profile: '', language: 'en-IN', channel: 'webrtc' })`)),
    { source: 'draft', tenantId: 't_9c21a4be', language: 'en-IN', channel: 'webrtc', agent: 'maya' });
  assert.equal(run(`previewBody({ source: 'live', tenantId: 't', language: 'hi', channel: 'telephony', profile: 'p' }).source`), 'live');
  assert.equal(run(`previewBody({ source: 'other', tenantId: 't', language: 'hi', channel: 'webrtc' }).source`), 'draft');
});

test('pending changes are summed up by what happened to each document', () => {
  assert.equal(run(`changeSummary([{ change: 'added' }, { change: 'changed' }, { change: 'added' }])`), '2 added, 1 changed');
});

test('history filters on actor, action, document and note', () => {
  const entry = `({ actor: 'Asha', action: 'rollback', release: 3, note: 'bad near miss' })`;
  assert.equal(run(`historyMatches(${entry}, 'asha')`), true);
  assert.equal(run(`historyMatches(${entry}, 'rolled back')`), true);
  assert.equal(run(`historyMatches(${entry}, 'release 3')`), true);
  assert.equal(run(`historyMatches(${entry}, 'near miss')`), true);
  assert.equal(run(`historyMatches(${entry}, 'maya')`), false);
  assert.equal(run(`historySubject({ kind: 'agents', name: 'maya', revision: 7 }).href`), '#/agents/maya');
  assert.equal(run(`historySubject({ release: 3 }).text`), 'release 3');
});

test('releases list newest first', () => {
  assert.deepEqual(plain(run(`newestFirst([{ release: 1 }, { release: 3 }, { release: 2 }], 'release').map((r) => r.release)`)), [3, 2, 1]);
});
