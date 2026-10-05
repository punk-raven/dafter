const PROVIDER_NAMES = {
  sarvam: 'Sarvam',
  groq: 'Groq',
  google: 'Google',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  deepgram: 'Deepgram',
  elevenlabs: 'ElevenLabs',
  cartesia: 'Cartesia',
  silero: 'Silero',
};

const SUMMARY_LIMIT = 3;

const TURN_STRATEGIES = {
  auto: 'automatic',
  vad: 'silence',
  provider_endpointing: 'speech service',
  semantic: 'meaning',
  server_vad: 'model',
  manual: 'manual',
};

const ADDRESSING_MODES = { always: 'answers every turn', transcript: 'answers when named', on_device: 'answers when named on device' };

function countOf(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function at(doc, path) {
  let node = doc;
  for (const key of path.split('.')) {
    if (!isObject(node) || !(key in node)) return undefined;
    node = node[key];
  }
  return node;
}

function providerName(id) {
  const key = String(id || '');
  return PROVIDER_NAMES[key] || key.charAt(0).toUpperCase() + key.slice(1);
}

function stageLabel(stage) {
  if (!isObject(stage) || !stage.provider) return '';
  return [providerName(stage.provider), stage.model].filter(Boolean).join(' ');
}

function credentialOf(...stages) {
  const stage = stages.find((s) => isObject(s) && typeof s.credentialRef === 'string');
  return stage ? `key\u00a0${stage.credentialRef}` : '';
}

function pipelineFacts(layer) {
  const pipeline = at(layer, 'agent.pipeline') || {};
  const facts = [];
  if (isObject(pipeline.stt) && pipeline.stt.provider) {
    const mode = at(pipeline.stt, 'options.mode');
    facts.push(`speech-to-text ${stageLabel(pipeline.stt)}${mode ? ` (${mode})` : ''}`);
  }
  if (isObject(pipeline.tts) && pipeline.tts.provider) {
    const voice = at(pipeline.tts, 'options.voice');
    facts.push(voice ? `voice ${voice} (${stageLabel(pipeline.tts)})` : `text-to-speech ${stageLabel(pipeline.tts)}`);
  }
  if (isObject(pipeline.llm) && pipeline.llm.provider) facts.push(`LLM ${stageLabel(pipeline.llm)}`);
  if (isObject(pipeline.realtime) && pipeline.realtime.provider) facts.push(`realtime ${stageLabel(pipeline.realtime)}`);
  return facts;
}

function sampleRate(layer) {
  const rate = at(layer, 'agent.pipeline.stt.options.sampleRate') || at(layer, 'agent.pipeline.tts.options.sampleRate');
  return typeof rate === 'number' ? `audio ${rate / 1000} kHz` : '';
}

function videoFact(layer) {
  const video = at(layer, 'media.video');
  if (!isObject(video)) return '';
  if (video.enabled === false) return 'video off';
  return video.resolution ? `video ${String(video.resolution).replace(/^h/, '')}p` : '';
}

function recordingFact(layer) {
  const egress = at(layer, 'media.egress');
  if (isObject(egress) && egress.width && egress.height) return `recordings ${egress.width}×${egress.height}${egress.framerate ? ` at ${egress.framerate} fps` : ''}`;
  if (isObject(egress) && egress.audioBitrate) return `recording audio ${egress.audioBitrate} kbps`;
  return '';
}

function behaviourFacts(layer) {
  const facts = [];
  const greets = at(layer, 'agent.greets');
  if (greets === true) facts.push('agent speaks first');
  if (greets === false) facts.push('agent waits to be spoken to');
  const mode = at(layer, 'agent.addressing.mode');
  if (mode) facts.push(ADDRESSING_MODES[mode] || `addressing ${mode}`);
  const strategy = at(layer, 'turn.strategy');
  if (strategy) facts.push(`end of turn: ${TURN_STRATEGIES[strategy] || strategy}`);
  const silence = at(layer, 'turn.silenceMs');
  if (typeof silence === 'number') facts.push(`waits ${silence} ms of silence`);
  return facts;
}

function layerFacts(layer) {
  const facts = [];
  if (at(layer, 'agent.name')) facts.push(`agent ${at(layer, 'agent.name')}`);
  if (at(layer, 'privacyMode')) facts.push(`privacy ${at(layer, 'privacyMode')}`);
  if (typeof layer.llm === 'string') facts.push(`LLM route ${layer.llm}`);
  const persona = at(layer, 'agent.personaRef');
  if (persona) facts.push(`persona ${String(persona).replace(/^persona:\/\//, '')}`);
  facts.push(...pipelineFacts(layer));
  const scribeOn = at(layer, 'scribe.enabled') !== false;
  const scribe = scribeOn ? at(layer, 'scribe.llm') : undefined;
  const judge = scribeOn ? at(layer, 'scribe.judge') : undefined;
  if (stageLabel(scribe)) facts.push(`notes by ${stageLabel(scribe)}`);
  if (stageLabel(judge)) facts.push(`turns graded by ${stageLabel(judge)}`);
  const regions = at(layer, 'residency.allowedRegions');
  if (Array.isArray(regions) && regions.length) facts.push(`data stays in ${regions.join(', ')}`);
  const budget = at(layer, 'budgets.maxSessionCostUsd');
  if (typeof budget === 'number') facts.push(`at most $${budget.toFixed(2)} a session`);
  const trunk = at(layer, 'telephony.trunk');
  if (trunk) facts.push(`phone trunk ${trunk}`);
  for (const fact of [videoFact(layer), sampleRate(layer)]) if (fact) facts.push(fact);
  facts.push(...behaviourFacts(layer));
  const recording = at(layer, 'recording.enabled');
  if (typeof recording === 'boolean') facts.push(recording ? 'recording on' : 'recording off');
  const transcription = at(layer, 'transcription.mode');
  if (transcription) facts.push(transcription === 'off' ? 'no transcript' : `transcript ${transcription.replace(/_/g, ' ')}`);
  const egress = recordingFact(layer);
  if (egress) facts.push(egress);
  return facts;
}

function agentSummary(d) {
  const parts = [];
  if (d.profile) parts.push(`profile ${d.profile}`);
  parts.push(countOf((d.aliases || []).length, 'alias', 'aliases'));
  parts.push(countOf((d.nearMisses || []).length, 'near miss', 'near misses'));
  return parts;
}

function llmSummary(d) {
  return [providerName(d.provider), d.model, d.region, credentialOf(d)].filter(Boolean);
}

function profileSummary(d) {
  const facts = layerFacts(d).slice(0, SUMMARY_LIMIT - 1);
  const key = credentialOf(at(d, 'agent.pipeline.llm'), at(d, 'scribe.llm'), at(d, 'scribe.judge'), at(d, 'agent.pipeline.realtime'));
  return key ? facts.concat(key) : layerFacts(d).slice(0, SUMMARY_LIMIT);
}

function merged(a, b) {
  const out = { ...a };
  for (const [key, value] of Object.entries(b)) out[key] = isObject(value) && isObject(out[key]) ? merged(out[key], value) : value;
  return out;
}

function axisSummary(kind, d) {
  const layer = merged(isObject(d.overlay) ? d.overlay : {}, isObject(d.tuning) ? d.tuning : {});
  const facts = layerFacts(layer);
  const first = kind === 'languages' ? facts.filter((f) => /^(speech-to-text|voice|text-to-speech|end of turn)/.test(f)) : [];
  return [...new Set(first.concat(facts))].slice(0, SUMMARY_LIMIT);
}

function docSummaryParts(kind, doc) {
  const d = isObject(doc) ? doc : {};
  if (kind === 'agents') return agentSummary(d);
  if (kind === 'llms') return llmSummary(d);
  if (kind === 'profiles') return profileSummary(d);
  if (kind === 'languages' || kind === 'channels') return axisSummary(kind, d);
  return layerFacts(d).slice(0, SUMMARY_LIMIT);
}

function docSummary(kind, doc) {
  return docSummaryParts(kind, doc).join(' · ');
}

const RELATIVE_STEPS = [
  [60, 'second'],
  [60, 'minute'],
  [24, 'hour'],
  [7, 'day'],
  [4.35, 'week'],
  [12, 'month'],
  [Infinity, 'year'],
];

function relativeTime(iso, now = Date.now()) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  let amount = Math.max(0, (now - then) / 1000);
  if (amount < 45) return 'just now';
  for (const [size, unit] of RELATIVE_STEPS) {
    if (amount < size) {
      const n = Math.max(1, Math.floor(amount));
      return `${n} ${unit}${n === 1 ? '' : 's'} ago`;
    }
    amount /= size;
  }
  return '';
}

function updatedLine(view, now) {
  const when = relativeTime(view.updatedAt, now);
  if (!when) return '';
  const by = view.updatedBy === 'catalog.json' ? 'from catalog.json' : view.updatedBy ? `by ${view.updatedBy}` : '';
  return `updated ${when}${by ? ` ${by}` : ''}`;
}

function humanKey(key) {
  const words = String(key).replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function glanceRows(kind, doc) {
  const d = isObject(doc) ? doc : {};
  if (kind !== 'llms') return [];
  const rows = [['Provider', d.provider ? providerName(d.provider) : ''], ['Model', d.model], ['Region', d.region], ['Key', d.credentialRef]];
  for (const [key, value] of Object.entries(isObject(d.options) ? d.options : {})) rows.push([humanKey(key), isObject(value) || Array.isArray(value) ? JSON.stringify(value) : String(value)]);
  return rows.filter(([, value]) => value !== undefined && value !== '');
}
