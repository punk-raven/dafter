import assert from 'node:assert/strict';
import test from 'node:test';
import { load, room } from './harness.mjs';

test('the log names the agent that took a note', () => {
  const run = load('agent.js', 'client-scribe.js');
  run('agentView').room = room('Nivya');
  assert.equal(run('noteTakenText()'), 'Nivya took a note');
  run('agentView').room = room('');
  assert.equal(run('noteTakenText()'), 'the agent took a note');
});
