import assert from 'node:assert/strict';
import test from 'node:test';
import { load, room } from './harness.mjs';

const client = () => load('agent.js', 'agent-turns.js', 'client-captions.js');

test('the agent\'s caption lines carry the name it joined under', () => {
  const run = client();
  run('captionView').room = room('Nivya');
  assert.equal(run('captionSpeaker({ kind: "agent" })').label, 'Nivya');
  assert.equal(run('captionSpeaker({ kind: "human", participantId: "p_4b81e0d7" })').label, 'You');
  assert.equal(run('captionSpeaker({ kind: "human", participantId: "p_9c2e11aa" })').label, 'p_9c2e11aa');
});

test('an agent that joined without a name is captioned as the agent', () => {
  const run = client();
  run('captionView').room = room('');
  assert.equal(run('captionSpeaker({ kind: "agent" })').label, 'Agent');
});

test('the Agent panel\'s transcript labels the agent by the name it joined under', () => {
  const run = client();
  const call = room('Nivya');
  assert.equal(run('transcriptSpeaker')(call, 'agent-AJ_7f3a9c21').label, 'Nivya');
  assert.equal(run('transcriptSpeaker')(room(''), 'agent-AJ_7f3a9c21').label, 'Agent');
  assert.equal(run('transcriptSpeaker')(call, 'p_4b81e0d7').label, 'You');
});
