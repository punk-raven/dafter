const SPEECH_TOGGLES = {
  fillers: {
    label: 'Fillers',
    title: 'a short phrase such as "hmm, one second" when a reply is slow',
    on: false,
    apply: (o, on) => deepAssign(o, { agent: { speech: { fillers: { enabled: on } } } }),
  },
  backchannel: {
    label: 'Backchannel',
    title: 'your "haan" or "hmm" while Nivya speaks is heard as listening, not as a new turn',
    on: true,
    apply: (o, on) => deepAssign(o, { turn: { interruption: { backchannel: { enabled: on } } } }),
  },
  normalization: {
    label: 'Normalization',
    title: 'numbers, dates, prices and phone numbers rewritten as spoken words before TTS (on: the platform planner; off: TTS reads what the LLM wrote)',
    on: true,
    apply: (o, on) => deepAssign(o, { agent: { speech: { normalization: on ? 'platform' : 'provider' } } }),
  },
};

const SPEECH_STORAGE_PREFIX = 'dafter.speech.';

function deepAssign(target, source) {
  for (const [key, value] of Object.entries(source)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      if (!target[key] || typeof target[key] !== 'object') target[key] = {};
      deepAssign(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

function rememberedToggle(name) {
  try {
    const stored = localStorage.getItem(SPEECH_STORAGE_PREFIX + name);
    return stored === null ? SPEECH_TOGGLES[name].on : stored === 'on';
  } catch {
    return SPEECH_TOGGLES[name].on;
  }
}

function fillSpeechToggles() {
  const el = document.getElementById('speech-toggles');
  for (const [name, toggle] of Object.entries(SPEECH_TOGGLES)) {
    const label = document.createElement('label');
    label.className = 'speech-toggle';
    label.title = toggle.title;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.id = `speech-${name}`;
    box.checked = rememberedToggle(name);
    box.onchange = () => rememberToggle(name);
    const text = document.createElement('span');
    text.textContent = toggle.label;
    label.appendChild(box);
    label.appendChild(text);
    el.appendChild(label);
  }
}

function rememberToggle(name) {
  try {
    localStorage.setItem(SPEECH_STORAGE_PREFIX + name, speechToggle(name) ? 'on' : 'off');
  } catch {
    log('this browser will not remember the speech toggles', 'warn');
  }
}

function speechToggle(name) {
  const box = document.getElementById(`speech-${name}`);
  return box ? box.checked : SPEECH_TOGGLES[name].on;
}

function speechOverrides(overrides) {
  for (const [name, toggle] of Object.entries(SPEECH_TOGGLES)) toggle.apply(overrides, speechToggle(name));
  return overrides;
}

function askedSpeech(config) {
  const speech = (config && config.agent && config.agent.speech) || {};
  const backchannel = (config && config.turn && config.turn.interruption && config.turn.interruption.backchannel) || {};
  return {
    fillers: !speech.fillers || speech.fillers.enabled !== false,
    backchannel: backchannel.enabled !== false,
    normalization: speech.normalization !== 'provider',
  };
}

function renderAgentSpeech(config, effective) {
  const el = document.getElementById('agent-speech');
  if (!el) return;
  if (!effective) {
    el.textContent = '';
    el.className = 'agent-speech';
    return;
  }
  const asked = askedSpeech(config);
  const actual = {
    fillers: effective.fillers,
    backchannel: effective.backchannel,
    normalization: effective.normalization === 'platform',
  };
  el.replaceChildren(...Object.entries(SPEECH_TOGGLES).map(([name, toggle]) => {
    const item = document.createElement('span');
    const differs = actual[name] !== asked[name];
    item.className = `agent-speech-item${actual[name] ? ' on' : ''}${differs ? ' differs' : ''}`;
    item.textContent = `${toggle.label} ${actual[name] ? 'on' : 'off'}`;
    item.title = differs
      ? `the session asked for ${asked[name] ? 'on' : 'off'}; the agent has nothing to apply it with in this language`
      : `the agent reported ${toggle.label.toLowerCase()} ${actual[name] ? 'on' : 'off'}`;
    return item;
  }));
  el.className = 'agent-speech';
}
