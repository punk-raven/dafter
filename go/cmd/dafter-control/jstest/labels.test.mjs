import assert from 'node:assert/strict';
import test from 'node:test';
import { load, room } from './harness.mjs';

const client = () => load('client-names.js', 'agent.js', 'agent-turns.js', 'client-captions.js');

test('the agent\'s caption lines carry the name it joined under', () => {
  const run = client();
  run('captionView').room = room('Nivya');
  assert.equal(run('captionSpeaker({ kind: "agent" })').label, 'Nivya');
  assert.equal(run('captionSpeaker({ kind: "human", participantId: "p_4b81e0d7" })').label, 'You');
  assert.equal(run('captionSpeaker({ kind: "human", participantId: "p_9c2e11aa" })').label, 'Guest');
});

test('a person is captioned and transcribed by the name they shared, never their participant id', () => {
  const run = client();
  run('captionView').room = room('Nivya');
  run('displayNames').set('p_9c2e11aa', 'Asha');
  assert.equal(run('captionSpeaker({ kind: "human", participantId: "p_9c2e11aa" })').label, 'Asha');
  assert.equal(run('transcriptSpeaker')(room('Nivya'), 'p_9c2e11aa').label, 'Asha');
  assert.equal(run('nameOf')('p_00000000', room('Nivya')), 'Guest');
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

test('a late joiner reads the agent\'s state from the attribute the agent keeps, and ignores anything else there', () => {
  const run = client();
  const g = run('globalThis');
  const states = [];
  g.setAgentTileState = (state) => states.push(state);
  g.renderAgentControls = () => {};
  run('followAgentAttributes')({ attributes: { 'lk.agent.state': 'listening' } });
  run('followAgentAttributes')({ attributes: { 'lk.agent.state': 'listening' } });
  run('followAgentAttributes')({ attributes: { 'lk.agent.state': '<b>hi</b>' } });
  run('followAgentAttributes')({ attributes: {} });
  assert.equal(run('agentView').state, 'listening');
  assert.deepEqual([...states], ['listening']);
});
