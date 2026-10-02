const LLM_PROVIDERS = { sarvam: 'Sarvam', groq: 'Groq', google: 'Google AI Studio', openrouter: 'OpenRouter' };

const LLMS = [
  'sarvam/sarvam-105b',
  'groq/qwen/qwen3.8-27b',
  'groq/openai/gpt-oss-20b',
  'groq/openai/gpt-oss-120b',
  'google/gemma-4-26b-a4b-it',
  'google/gemma-4-31b-it',
  'openrouter/qwen/qwen3.8-flash',
  'openrouter/nvidia/nemotron-3-super-120b-a12b:free',
  'openrouter/inclusionai/ling-3.0-flash-sante:free',
  'openrouter/liquid/lfm-2.5-2.6b:free',
];

const LLM_STORAGE_KEY = 'dafter.llm';

function llmProvider(route) {
  return route.slice(0, route.indexOf('/'));
}

function llmModel(route) {
  return route.slice(route.indexOf('/') + 1);
}

function llmName(route) {
  const model = llmModel(route);
  return model.slice(model.lastIndexOf('/') + 1);
}

function llmProviderName(route) {
  const provider = llmProvider(route);
  return LLM_PROVIDERS[provider] || provider;
}

function rememberedLlm() {
  try {
    const stored = localStorage.getItem(LLM_STORAGE_KEY);
    return LLMS.includes(stored) ? stored : LLMS[0];
  } catch {
    return LLMS[0];
  }
}

function chooseLlm() {
  const route = chosenLlm();
  document.getElementById('llm-provider').textContent = llmProviderName(route);
  document.getElementById('llm').title = route;
}

function rememberLlm() {
  chooseLlm();
  try {
    localStorage.setItem(LLM_STORAGE_KEY, chosenLlm());
  } catch {
    log('this browser will not remember the LLM choice', 'warn');
  }
}

function fillLlmChoices() {
  const el = document.getElementById('llm');
  const groups = new Map();
  for (const route of LLMS) {
    const provider = llmProvider(route);
    if (!groups.has(provider)) {
      const group = document.createElement('optgroup');
      group.label = llmProviderName(route);
      groups.set(provider, group);
      el.appendChild(group);
    }
    const opt = document.createElement('option');
    opt.value = route;
    opt.textContent = llmName(route);
    opt.title = route;
    groups.get(provider).appendChild(opt);
  }
  el.value = rememberedLlm();
  chooseLlm();
}

function chosenLlm() {
  return document.getElementById('llm').value || LLMS[0];
}

function configuredLlm(config) {
  const ref = config && config.agent && config.agent.pipeline && config.agent.pipeline.llm;
  return ref ? `${ref.provider}/${ref.model}` : null;
}

function servedLlms(items) {
  return [...new Set(items.filter((i) => i.stage === 'llm').map((i) => `${i.provider}/${i.model}`))];
}

function reportedLlm(effective) {
  return effective && effective.llm ? [`${effective.llm.provider}/${effective.llm.model}`] : [];
}

function renderAgentLlm(config, served) {
  const el = document.getElementById('agent-llm');
  if (!el) return;
  const asked = configuredLlm(config);
  if (!asked) {
    el.className = 'agent-llm';
    el.textContent = '';
    return;
  }
  if (!served.length) {
    el.textContent = `LLM ${asked}`;
    el.title = 'from the session config; waiting for the agent to report the LLM it built';
    el.className = 'agent-llm agent-llm-asked';
    return;
  }
  const match = served.length === 1 && served[0] === asked;
  el.textContent = match ? `LLM ${asked}` : `LLM ${served.join(', ')} - config asked ${asked}`;
  el.title = match ? 'the agent reported running the LLM the session config names' : 'the agent ran a different LLM from the one the session config names';
  el.className = `agent-llm ${match ? 'agent-llm-served' : 'agent-llm-mismatch'}`;
}
