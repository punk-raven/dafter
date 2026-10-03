import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const run = load('admin-api.js', 'admin-fields.js', 'admin-diff.js', 'admin-agent.js', 'admin-list.js', 'admin-docs.js', 'admin-publish.js', 'admin-releases.js', 'admin.js');
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

test('editable kinds offer edit and delete on each row, git kinds only view', () => {
  for (const kind of ['agents', 'profiles', 'tenants', 'languages', 'channels']) {
    assert.deepEqual(plain(run(`rowActionsFor(kindInfo('${kind}'))`)), ['edit', 'delete'], kind);
  }
  for (const kind of ['defaults', 'llms']) {
    assert.deepEqual(plain(run(`rowActionsFor(kindInfo('${kind}'))`)), ['view'], kind);
  }
});

test('a removed document says whether its removal is still waiting to be published', () => {
  assert.deepEqual(plain(run(`removedStatus({ live: true })`)), { label: 'Pending removal', tone: 'removed' });
  assert.deepEqual(plain(run(`removedStatus({ live: false })`)), { label: 'Removed', tone: 'muted' });
  const meta = run(`removedMeta({ deletedAt: '2026-10-03T09:30:00Z', deletedBy: 'Asha', revision: 12, note: 'retired' })`);
  assert.match(meta, /^deleted 2026-10-0\d \d\d:\d\d by Asha · last revision 12 · "retired"$/);
  assert.match(run(`removedMeta({ deletedAt: '2026-10-03T09:30:00Z', deletedBy: '', revision: 3 })`), /by admin · last revision 3$/);
});

test('previously used words come from earlier revisions, newest first, minus what the lists hold now', () => {
  const history = JSON.stringify([
    { name: 'Maya', aliases: ['Mya', 'Maaya'], nearMisses: ['Maia'] },
    { name: 'Maya', aliases: ['Mya', 'Myah'], nearMisses: ['Maia', 'Mira'] },
    { name: 'Maya', aliases: ['mya'], nearMisses: [' '] },
  ]);
  assert.deepEqual(plain(run(`previouslyUsed(${history}, 'aliases', ['MYA'])`)), ['Myah', 'Maaya']);
  assert.deepEqual(plain(run(`previouslyUsed(${history}, 'nearMisses', ['Maia'])`)), ['Mira']);
  assert.deepEqual(plain(run(`previouslyUsed(${history}, 'aliases', ['mya', 'Myah', 'Maaya'])`)), []);
  assert.deepEqual(plain(run(`previouslyUsed(${history}, 'nearMisses', ['Maia'].concat(['Mira']))`)), [], 'a word now an alias is not offered as a near miss');
  assert.deepEqual(plain(run(`previouslyUsed([], 'aliases', [])`)), []);
  assert.deepEqual(plain(run(`previouslyUsed([{ aliases: 'not a list' }, null], 'aliases', [])`)), []);
});
