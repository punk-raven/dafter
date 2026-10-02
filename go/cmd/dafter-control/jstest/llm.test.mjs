import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { load } from './harness.mjs';

const catalog = JSON.parse(readFileSync(new URL('../catalog.json', import.meta.url), 'utf8'));

function element(id) {
  return {
    id, value: '', textContent: '', title: '', className: '', label: '', children: [],
    appendChild(child) { this.children.push(child); },
  };
}

function page(storage) {
  const elements = new Map();
  const logs = [];
  const run = load('agent-llm.js');
  Object.assign(run('globalThis'), {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id));
        return elements.get(id);
      },
      createElement: () => element(''),
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

const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };

test('the picker offers exactly the routes the catalog carries, Sarvam first', () => {
  const { run } = page(memory());
  const routes = run('LLMS');
  assert.deepEqual([...routes].sort(), Object.keys(catalog.llms).sort());
  assert.equal(routes[0], 'sarvam/sarvam-105b');
  assert.equal(new Set(routes).size, routes.length);
  for (const route of routes) assert.ok(run(`LLM_PROVIDERS['${route.split('/')[0]}']`), `${route} has no provider name`);
});

test('the options are grouped by provider and default to Sarvam', () => {
  const { run, elements } = page(memory());
  run('fillLlmChoices()');
  const select = elements.get('llm');
  assert.deepEqual(select.children.map((g) => g.label), ['Sarvam', 'Groq', 'Google AI Studio', 'OpenRouter']);
  assert.deepEqual(select.children[1].children.map((o) => [o.value, o.textContent]), [
    ['groq/qwen/qwen3.8-27b', 'qwen3.8-27b'],
    ['groq/openai/gpt-oss-20b', 'gpt-oss-20b'],
    ['groq/openai/gpt-oss-120b', 'gpt-oss-120b'],
  ]);
  assert.equal(select.value, 'sarvam/sarvam-105b');
  assert.equal(elements.get('llm-provider').textContent, 'Sarvam');
  select.value = 'openrouter/nvidia/nemotron-3-super-120b-a12b:free';
  run('chooseLlm()');
  assert.equal(elements.get('llm-provider').textContent, 'OpenRouter');
  assert.equal(select.title, 'openrouter/nvidia/nemotron-3-super-120b-a12b:free');
});

test('the last choice is remembered per browser, and a stale or unreadable one falls back to Sarvam', () => {
  const storage = memory({ 'dafter.llm': 'groq/openai/gpt-oss-20b' });
  const { run, elements } = page(storage);
  run('fillLlmChoices()');
  assert.equal(elements.get('llm').value, 'groq/openai/gpt-oss-20b');
  elements.get('llm').value = 'openrouter/qwen/qwen3.8-flash';
  run('rememberLlm()');
  assert.equal(storage.values.get('dafter.llm'), 'openrouter/qwen/qwen3.8-flash');

  const stale = page(memory({ 'dafter.llm': 'groq/retired-model' }));
  stale.run('fillLlmChoices()');
  assert.equal(stale.elements.get('llm').value, 'sarvam/sarvam-105b');

  const blocked = page(broken);
  blocked.run('fillLlmChoices()');
  assert.equal(blocked.elements.get('llm').value, 'sarvam/sarvam-105b');
  blocked.run('rememberLlm()');
  assert.equal(blocked.logs.length, 1);
});

const config = { agent: { pipeline: { llm: { provider: 'groq', model: 'qwen/qwen3.8-27b' } } } };
const usage = (provider, model) => [
  { stage: 'stt', provider: 'sarvam', model: 'saaras:v3-realtime' },
  { stage: 'llm', provider, model, unit: 'input_token' },
  { stage: 'llm', provider, model, unit: 'output_token' },
];

test('the page names the LLM the agent reported, not the dropdown, and flags a mismatch', () => {
  const { run, elements } = page(memory());
  const el = () => elements.get('agent-llm');
  const show = (served) => run(`renderAgentLlm(${JSON.stringify(config)}, ${JSON.stringify(served)})`);
  show([]);
  assert.equal(el().textContent, 'LLM groq/qwen/qwen3.8-27b');
  assert.equal(el().className, 'agent-llm agent-llm-asked');

  show(run(`reportedLlm({ llm: { provider: 'groq', model: 'qwen/qwen3.8-27b' } })`));
  assert.equal(el().className, 'agent-llm agent-llm-served');

  show(run(`servedLlms(${JSON.stringify(usage('groq', 'qwen/qwen3.8-27b'))})`));
  assert.equal(el().className, 'agent-llm agent-llm-served');

  show(run(`servedLlms(${JSON.stringify(usage('sarvam', 'sarvam-105b'))})`));
  assert.equal(el().textContent, 'LLM sarvam/sarvam-105b - config asked groq/qwen/qwen3.8-27b');
  assert.equal(el().className, 'agent-llm agent-llm-mismatch');
});
