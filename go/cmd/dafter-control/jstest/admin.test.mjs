import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const SCRIPTS = ['admin-api.js', 'admin-fields.js', 'admin-diff.js'];

function panel(storage) {
  const run = load(...SCRIPTS);
  if (storage) run('globalThis').sessionStorage = storage;
  return run;
}

function memory() {
  const values = new Map();
  return {
    getItem: (k) => (values.has(k) ? values.get(k) : null),
    setItem: (k, v) => values.set(k, String(v)),
    removeItem: (k) => values.delete(k),
    values,
  };
}

const denied = {
  getItem() { throw new Error('denied'); },
  setItem() { throw new Error('denied'); },
  removeItem() { throw new Error('denied'); },
};

const plain = (value) => JSON.parse(JSON.stringify(value));

test('every request carries the admin token as a bearer credential', () => {
  const run = panel();
  const req = plain(run(`adminRequest('GET', adminPath('agents'), { token: 'dev-admin-token' })`));
  assert.equal(req.url, '/admin/v1/agents');
  assert.equal(req.headers.Authorization, 'Bearer dev-admin-token');
  assert.equal(req.body, undefined);
  assert.equal(req.headers['Content-Type'], undefined);
});

test('the actor and note travel as one-line headers and are left out when empty', () => {
  const run = panel();
  const req = plain(run(`adminRequest('POST', '/admin/v1/releases', { token: 't', actor: ' Asha ', note: 'ship\\nmaya\\t' })`));
  assert.equal(req.headers['X-Dafter-Actor'], 'Asha');
  assert.equal(req.headers['X-Dafter-Note'], 'ship maya');
  const bare = plain(run(`adminRequest('POST', '/admin/v1/releases', { token: 't', actor: '', note: '  ' })`));
  assert.equal('X-Dafter-Actor' in bare.headers, false);
  assert.equal('X-Dafter-Note' in bare.headers, false);
});

test('a body is sent as JSON with its content type', () => {
  const run = panel();
  const req = plain(run(`adminRequest('PUT', adminPath('agents', 'maya'), { token: 't', body: { name: 'Maya' } })`));
  assert.equal(req.headers['Content-Type'], 'application/json');
  assert.equal(req.body, '{"name":"Maya"}');
});

test('path segments are escaped so a name cannot reach another route', () => {
  const run = panel();
  assert.equal(run(`adminPath('agents', '../releases')`), '/admin/v1/agents/..%2Freleases');
  assert.equal(run(`adminPath('releases', '3', 'rollback')`), '/admin/v1/releases/3/rollback');
});

test('the token is kept for the tab only and survives a broken store', () => {
  const store = memory();
  const run = panel(store);
  run(`adminAuth.signIn(' dev-admin-token ', 'Asha')`);
  assert.equal(store.values.get('dafter.admin.token'), 'dev-admin-token');
  assert.equal(run('adminAuth.restore()'), true);
  run('adminAuth.signOut()');
  assert.equal(store.values.has('dafter.admin.token'), false);
  assert.equal(store.values.get('dafter.admin.actor'), 'Asha');

  const broken = panel(denied);
  broken(`adminAuth.signIn('t', 'a')`);
  assert.equal(broken('adminAuth.token'), 't');
  assert.equal(broken('adminAuth.restore()'), false);
});

test('an error detail splits into its JSON pointer and message', () => {
  const run = panel();
  assert.deepEqual(plain(run(`parseDetail("at '/agents/maya/nearMisses/0': minLength: got 0, want 1")`)),
    { pointer: '/agents/maya/nearMisses/0', message: 'minLength: got 0, want 1' });
  assert.deepEqual(plain(run(`parseDetail('the document is not JSON')`)), { pointer: null, message: 'the document is not JSON' });
});

test('document pointers map onto agent form fields and list items', () => {
  const run = panel();
  assert.deepEqual(plain(run(`agentFieldOf('agents', 'maya', '/agents/maya/nearMisses/2')`)), { field: 'nearMisses', index: 2 });
  assert.deepEqual(plain(run(`agentFieldOf('agents', 'maya', '/agents/maya/name')`)), { field: 'name', index: null });
  assert.deepEqual(plain(run(`agentFieldOf('agents', 'maya', '/agent/addressing/nearMisses')`)), { field: 'nearMisses', index: null });
  assert.equal(run(`agentFieldOf('agents', 'maya', '/agents/other/name')`), null);
  assert.equal(run(`agentFieldOf('profiles', 'maya', '/profiles/maya/name')`), null);
  assert.equal(run(`agentFieldOf('agents', 'maya', '/agents/maya/bogus')`), null);
});

test('pointers with escaped tokens resolve to the right document', () => {
  const run = panel();
  assert.equal(run(`documentPointer('llms', 'google/gemma', '/llms/google~1gemma/options')`), '/options');
  assert.equal(run(`documentPointer('agents', 'maya', '/agents/maya')`), '');
  assert.equal(run(`documentPointer('agents', 'maya', '/agents/mayan/name')`), null);
});

test('API problems land next to the fields they name, the rest stay listed', () => {
  const run = panel();
  const placed = plain(run(`placeProblems('agents', 'maya', [
    "at '/agents/maya/nearMisses/0': minLength: got 0, want 1",
    "at '/agents/maya/aliases': items at 0 and 1 are equal",
    "at '/agent/addressing/nearMisses': a near miss is a word that must never wake the agent",
    "at '/agents/maya/bogus': false schema",
    "at '/agents/maya': missing property 'name'",
    'the document is not JSON',
  ])`));
  assert.deepEqual(placed.items, { 'nearMisses/0': ['minLength: got 0, want 1'] });
  assert.deepEqual(placed.fields, {
    aliases: ['items at 0 and 1 are equal'],
    nearMisses: ['a near miss is a word that must never wake the agent'],
  });
  assert.deepEqual(placed.general, [
    { pointer: '/bogus', message: 'false schema' },
    { pointer: '/', message: "missing property 'name'" },
    { pointer: null, message: 'the document is not JSON' },
  ]);
});

test('schema wording is rephrased for people', () => {
  const run = panel();
  assert.equal(run(`friendlyMessage('minLength: got 0, want 1')`), 'cannot be empty');
  assert.equal(run(`friendlyMessage('false schema')`), 'is not a field this document accepts');
  assert.equal(run(`friendlyMessage('maxItems: got 9, want 8')`), 'has 9 entries, at most 8 allowed');
  assert.equal(run(`friendlyMessage('contradicts itself (resolving tenant t_1, agent maya) and 59 other combination(s)')`),
    'contradicts itself. Found resolving tenant t_1, agent maya and 59 other session combinations.');
  assert.equal(run(`friendlyMessage('no profile of that name (resolving tenant t_1)')`), 'no profile of that name. Found resolving tenant t_1.');
});

test('a line diff keeps unchanged lines and marks additions and removals', () => {
  const run = panel();
  const lines = plain(run(`lineDiff('a\\nb\\nc', 'a\\nB\\nc\\nd')`));
  assert.deepEqual(lines.map((l) => l.op + l.text), ['samea', 'delb', 'addB', 'samec', 'addd']);
  assert.deepEqual(plain(run(`diffStats(lineDiff('a\\nb\\nc', 'a\\nB\\nc\\nd'))`)), { added: 2, removed: 1 });
});

test('long unchanged runs fold away beyond the context lines', () => {
  const run = panel();
  const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\\n');
  const after = before.replace('l10', 'L10');
  const folded = plain(run(`foldDiff(lineDiff('${before}', '${after}'), 2)`));
  assert.deepEqual(folded.map((l) => l.op), ['fold', 'same', 'same', 'del', 'add', 'same', 'same', 'fold']);
  assert.equal(folded[0].count, 8);
  assert.equal(folded[7].count, 7);
});

test('an added document diffs against nothing', () => {
  const run = panel();
  const out = plain(run(`documentDiff({ change: 'added', draft: { name: 'Maya' } })`));
  assert.deepEqual(out.stats, { added: 3, removed: 0 });
});

test('an error title reads as an instruction, not a server message', () => {
  const run = load(...SCRIPTS);
  assert.equal(run(`problemTitle('the agents document has 1 problem(s)')`), 'Fix 1 problem before saving.');
  assert.equal(run(`problemTitle('the agents document has 3 problem(s)')`), 'Fix 3 problems before saving.');
  assert.equal(run(`problemTitle('the admin API could not be reached')`), 'The admin API could not be reached');
});

test('a document is named by its kind and name, the one defaults document by itself', () => {
  const run = load(...SCRIPTS);
  assert.equal(run(`docLabel('agents', 'maya')`), 'agent maya');
  assert.equal(run(`docLabel('defaults', 'defaults')`), 'defaults');
});
