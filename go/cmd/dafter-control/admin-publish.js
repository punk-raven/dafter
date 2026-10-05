const CHANGE_LABELS = { added: 'Added', changed: 'Changed', removed: 'Removed' };
const previewChoice = { source: 'draft', tenantId: '', agent: '', profile: '', language: '', channel: 'webrtc' };

function previewBody(values) {
  const body = { source: values.source === 'live' ? 'live' : 'draft', tenantId: values.tenantId, language: values.language, channel: values.channel };
  if (values.agent) body.agent = values.agent;
  if (values.profile) body.profile = values.profile;
  return body;
}

function changeSummary(changes) {
  const counts = { added: 0, changed: 0, removed: 0 };
  for (const c of changes) counts[c.change] = (counts[c.change] || 0) + 1;
  return Object.entries(counts).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k}`).join(', ');
}

function changeBlock(change) {
  const { lines, stats } = documentDiff(change);
  return el('details', { class: 'change', open: '' },
    el('summary', {},
      el('span', { class: `pill pill-${change.change}` }, CHANGE_LABELS[change.change] || change.change),
      el('span', { class: 'change-name' }, docLabel(change.kind, change.name)),
      el('span', { class: 'stats', 'aria-label': `${stats.added} lines added, ${stats.removed} lines removed` },
        el('span', { class: 'plus' }, `+${stats.added}`), ' ', el('span', { class: 'minus' }, `-${stats.removed}`))),
    renderDiff(lines));
}

async function publishDraft(count, problems) {
  const answer = await confirmDialog({
    title: `Publish ${count === 1 ? '1 change' : `${count} changes`}?`,
    body: 'New sessions resolve against the new release within a few seconds. Sessions already running keep the config they started with.',
    confirmLabel: 'Publish', withNote: true,
  });
  if (!answer.ok) return;
  try {
    const release = await adminCall('POST', adminPath('releases'), { note: answer.note });
    notify(`Release ${release.release} is live.`);
    await refreshSummary();
    location.hash = '#/releases';
  } catch (err) {
    if (err.status === 401) return;
    problems.replaceChildren(problemList(err).box);
  }
}

async function showChanges() {
  const diff = await refreshSummary();
  const problems = el('div', {});
  const base = diff.liveRelease ? `release ${diff.liveRelease}` : 'nothing published yet';
  if (diff.changes.length === 0) {
    showMain(pageHead('Changes', `Everything saved is live (${base}).`),
      el('section', { class: 'card' }, el('p', { class: 'empty' }, 'Nothing waiting to be published. Saved edits wait here until you publish them.')));
    return;
  }
  const publish = el('button', { type: 'button', class: 'btn btn-primary' }, 'Publish');
  publish.addEventListener('click', () => publishDraft(diff.changes.length, problems));
  showMain(
    pageHead('Changes', `Saved edits that are not live yet (${changeSummary(diff.changes)}), compared with ${base}. Publishing makes them live for new sessions.`,
      el('a', { class: 'btn btn-quiet', href: '#/preview' }, 'Preview'), publish),
    problems,
    el('section', { class: 'card', 'aria-label': 'Changed documents' }, diff.changes.map(changeBlock)));
}

function choiceSelect(id, label, options, value, emptyLabel, hint) {
  const select = el('select', { id, 'aria-describedby': hint ? `${id}-hint` : null },
    emptyLabel ? el('option', { value: '' }, emptyLabel) : null,
    options.map((o) => el('option', { value: o.value }, o.label)));
  select.value = options.some((o) => o.value === value) || (emptyLabel && value === '') ? value : (options[0] ? options[0].value : '');
  return { select, node: el('div', { class: 'field' }, el('label', { for: id }, label), hint ? el('p', { class: 'hint', id: `${id}-hint` }, hint) : null, select) };
}

function choicesOf(kind, views) {
  return views.map((v) => {
    const shown = docTitle(kind, v);
    return { value: v.name, label: shown !== v.name ? `${shown} (${v.name})` : v.name };
  });
}

function previewStartingChoice(defaults, languages) {
  const preferred = at(defaults, 'agent.languageSwitching.languages') || [];
  const names = languages.map((v) => v.name);
  return preferred.find((tag) => names.includes(tag)) || names[0] || '';
}

function previewRows(config) {
  const agent = config.agent || {};
  const addressing = agent.addressing || {};
  const pipeline = agent.pipeline || {};
  const tts = pipeline.tts || {};
  const voice = at(tts, 'options.voice');
  return [
    ['Privacy', config.privacyMode],
    ['Agent', agent.enabled === false ? 'off' : agent.name],
    ['Persona', agent.personaRef ? String(agent.personaRef).replace(/^persona:\/\//, '') : ''],
    ['Speech-to-text', stageLabel(pipeline.stt)],
    ['LLM', stageLabel(pipeline.llm) || stageLabel(pipeline.realtime)],
    ['Voice', voice ? `${voice} (${stageLabel(tts)})` : stageLabel(tts)],
    ['End of turn', config.turn && config.turn.strategy ? TURN_STRATEGIES[config.turn.strategy] || config.turn.strategy : ''],
    ['Recording', config.recording && config.recording.enabled ? 'on' : 'off'],
    ['Transcript', config.transcription && config.transcription.mode && config.transcription.mode !== 'off' ? config.transcription.mode.replace(/_/g, ' ') : 'none'],
    ['Aliases', (addressing.aliases || []).join(', ') || 'none'],
    ['Near misses', (addressing.nearMisses || []).join(', ') || 'none'],
  ];
}

function previewSummary(config) {
  return el('dl', { class: 'summary' }, previewRows(config).flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v === undefined || v === '' ? '-' : String(v))]));
}

async function showPreview() {
  const [tenants, agents, profiles, languages, channels, defaults] = await Promise.all(
    ['tenants', 'agents', 'profiles', 'languages', 'channels', 'defaults'].map((k) => adminCall('GET', adminPath(k))));
  const baseline = defaults[0] ? defaults[0].document : {};
  if (!previewChoice.language) previewChoice.language = previewStartingChoice(baseline, languages);
  const channelViews = [...new Set(Object.keys(CHANNEL_LABELS).concat(channels.map((c) => c.name)))].map((name) => ({ name }));
  const defaultAgent = at(baseline, 'agent.name');
  const tenant = choiceSelect('preview-tenant', 'Tenant', choicesOf('tenants', tenants), previewChoice.tenantId);
  const language = choiceSelect('preview-language', 'Language', choicesOf('languages', languages), previewChoice.language);
  const channel = choiceSelect('preview-channel', 'Channel', choicesOf('channels', channelViews), previewChoice.channel);
  const agent = choiceSelect('preview-agent', 'Agent', choicesOf('agents', agents), previewChoice.agent,
    defaultAgent ? `Default agent (${defaultAgent})` : 'Default agent', 'The agent a session asks for by name, if any.');
  const profile = choiceSelect('preview-profile', 'Profile', choicesOf('profiles', profiles), previewChoice.profile,
    'The agent\'s own profile, or none', 'Only when a session names a profile itself.');
  const sources = ['draft', 'live'].map((s) => el('label', {},
    el('input', { type: 'radio', name: 'preview-source', value: s, checked: previewChoice.source === s || null }),
    s === 'draft' ? 'Draft, including unpublished edits' : `What is live now${adminState.liveRelease ? ` (release ${adminState.liveRelease})` : ''}`));
  const result = el('div', { 'aria-live': 'polite' });
  const run = el('button', { type: 'submit', class: 'btn btn-primary' }, 'Show settings');
  const form = el('form', { class: 'card', 'aria-label': 'Preview a session' },
    el('fieldset', { class: 'choice' }, el('legend', {}, 'Use'), el('div', { class: 'radios' }, sources)),
    el('div', { class: 'form-grid form-grid-3' }, tenant.node, language.node, channel.node, agent.node, profile.node),
    el('div', { class: 'actions' }, run));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    Object.assign(previewChoice, {
      source: form.querySelector('input[name="preview-source"]:checked').value,
      tenantId: tenant.select.value, agent: agent.select.value, profile: profile.select.value,
      language: language.select.value, channel: channel.select.value,
    });
    run.disabled = true;
    try {
      const out = await adminCall('POST', adminPath('preview'), { body: previewBody(previewChoice) });
      const copy = el('button', { type: 'button', class: 'btn btn-quiet btn-small' }, 'Copy');
      copy.addEventListener('click', () => navigator.clipboard.writeText(out.configHash).then(() => notify('Fingerprint copied.'), () => notify('The browser did not allow copying.', 'warn')));
      result.replaceChildren(el('section', { class: 'card', 'aria-label': 'Resolved settings' },
        el('h2', {}, out.source === 'live' ? `Settings from release ${out.release}` : 'Settings from the draft'),
        el('div', { class: 'hash' }, el('span', { class: 'label' }, 'Fingerprint'), el('code', {}, out.configHash), copy),
        el('p', { class: 'hint' }, 'The fingerprint (configHash) changes whenever any setting below does, so two sessions with the same fingerprint ran with identical settings.'),
        previewSummary(out.config),
        el('details', { class: 'raw-config' }, el('summary', {}, 'Full settings as JSON'),
          el('pre', { class: 'json', tabindex: '0', 'aria-label': 'Resolved session config' }, prettyJson(out.config)))));
    } catch (err) {
      if (err.status !== 401) result.replaceChildren(problemList(err).box);
    } finally {
      run.disabled = false;
    }
  });
  showMain(
    pageHead('Preview', 'See the exact settings a new session would get for these choices, before or after publishing.'),
    form, result);
}
