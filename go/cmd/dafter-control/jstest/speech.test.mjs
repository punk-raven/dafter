import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

function element(id) {
  return {
    id, type: '', checked: false, textContent: '', title: '', className: '', children: [],
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
  };
}

function page(storage) {
  const elements = new Map();
  const logs = [];
  const run = load('agent-speech.js');
  Object.assign(run('globalThis'), {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id));
        return elements.get(id);
      },
      createElement(tag) {
        const made = element('');
        return new Proxy(made, {
          set(target, key, value) {
            target[key] = value;
            if (key === 'id') elements.set(value, target);
            return true;
          },
        });
      },
    },
    localStorage: storage,
    log: (...args) => logs.push(args),
  });
  return { run, elements, logs };
}

function memory(initial) {
  const values = new Map(Object.entries(initial || {}));
  return { getItem: (k) => (values.has(k) ? values.get(k) : null), setItem: (k, v) => values.set(k, v), values };
}

test('fillers start off, backchannel and normalization on, and each choice is remembered', () => {
  const storage = memory();
  const { run, elements } = page(storage);
  run('fillSpeechToggles()');
  const box = (name) => elements.get(`speech-${name}`);
  assert.deepEqual(['fillers', 'backchannel', 'normalization'].map((n) => box(n).checked), [false, true, true]);
  box('fillers').checked = true;
  box('fillers').onchange();
  assert.equal(storage.values.get('dafter.speech.fillers'), 'on');

  const again = page(storage);
  again.run('fillSpeechToggles()');
  assert.equal(again.elements.get('speech-fillers').checked, true);

  const blocked = page({ getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } });
  blocked.run('fillSpeechToggles()');
  assert.equal(blocked.elements.get('speech-fillers').checked, false);
});

test('the toggles become session overrides of the existing config fields, beside other agent overrides', () => {
  const { run, elements } = page(memory({ 'dafter.speech.normalization': 'off' }));
  run('fillSpeechToggles()');
  const overrides = run(`speechOverrides({ agent: { addressing: { mode: 'always' } } })`);
  assert.deepEqual(JSON.parse(JSON.stringify(overrides)), {
    agent: { addressing: { mode: 'always' }, speech: { fillers: { enabled: false }, normalization: 'provider' } },
    turn: { interruption: { backchannel: { enabled: true } } },
  });
  assert.equal(elements.get('speech-normalization').checked, false);
});

test('the panel shows what the agent reported and marks a setting it could not apply', () => {
  const { run, elements } = page(memory());
  const config = { agent: { speech: { fillers: { enabled: false }, normalization: 'platform' } }, turn: { interruption: { backchannel: { enabled: true } } } };
  run(`renderAgentSpeech(${JSON.stringify(config)}, { fillers: false, backchannel: true, normalization: 'provider' })`);
  const items = elements.get('agent-speech').children;
  assert.deepEqual(items.map((i) => i.textContent), ['Fillers off', 'Backchannel on', 'Normalization off']);
  assert.deepEqual(items.map((i) => i.className), [
    'agent-speech-item', 'agent-speech-item on', 'agent-speech-item differs',
  ]);
  run(`renderAgentSpeech(${JSON.stringify(config)}, null)`);
  assert.equal(elements.get('agent-speech').textContent, '');
});
