const CHANGE_LABELS = { added: 'Added', changed: 'Changed', removed: 'Removed' };
const PREVIEW_CHANNELS = ['webrtc', 'telephony', 'long_form'];
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
  const info = kindInfo(change.kind);
  return el('details', { class: 'change', open: '' },
    el('summary', {},
      el('span', { class: `pill pill-${change.change}` }, CHANGE_LABELS[change.change] || change.change),
      el('span', { class: 'change-name' }, `${info ? info.single : change.kind} ${change.name}`),
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
    showMain(pageHead('Changes', `The draft matches what is live (${base}).`),
      el('section', { class: 'card' }, el('p', { class: 'empty' }, 'Nothing to publish. Edit a document to start a change.')));
    return;
  }
  const publish = el('button', { type: 'button', class: 'btn btn-primary' }, 'Publish');
  publish.addEventListener('click', () => publishDraft(diff.changes.length, problems));
  showMain(
    pageHead('Changes', `Draft compared with ${base}: ${changeSummary(diff.changes)}.`, el('a', { class: 'btn btn-quiet', href: '#/preview' }, 'Preview'), publish),
    problems,
    el('section', { class: 'card', 'aria-label': 'Changed documents' }, diff.changes.map(changeBlock)));
}

function choiceSelect(id, label, options, value, emptyLabel) {
  const select = el('select', { id },
    emptyLabel ? el('option', { value: '' }, emptyLabel) : null,
    options.map((o) => el('option', { value: o.value }, o.label)));
  select.value = options.some((o) => o.value === value) || (emptyLabel && value === '') ? value : (options[0] ? options[0].value : '');
  return { select, node: el('div', { class: 'field' }, el('label', { for: id }, label), select) };
}

function namesOf(views) {
  return views.map((v) => ({ value: v.name, label: v.document && v.document.name && v.kind === 'agents' ? `${v.document.name} (${v.name})` : v.name }));
}

function previewSummary(config) {
  const agent = config.agent || {};
  const addressing = agent.addressing || {};
  const rows = [
    ['Privacy', config.privacyMode],
    ['Agent', agent.enabled === false ? 'disabled' : agent.name],
    ['Aliases', (addressing.aliases || []).join(', ') || 'none'],
    ['Near misses', (addressing.nearMisses || []).join(', ') || 'none'],
  ];
  return el('dl', { class: 'summary' }, rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v === undefined ? '-' : String(v))]));
}

async function showPreview() {
  const [tenants, agents, profiles, languages, channels] = await Promise.all(
    ['tenants', 'agents', 'profiles', 'languages', 'channels'].map((k) => adminCall('GET', adminPath(k))));
  const channelNames = [...new Set(PREVIEW_CHANNELS.concat(channels.map((c) => c.name)))].map((c) => ({ value: c, label: c }));
  const tenant = choiceSelect('preview-tenant', 'Tenant', namesOf(tenants), previewChoice.tenantId);
  const agent = choiceSelect('preview-agent', 'Agent', namesOf(agents), previewChoice.agent, 'None (defaults)');
  const profile = choiceSelect('preview-profile', 'Profile', namesOf(profiles), previewChoice.profile, 'None (agent or defaults)');
  const language = choiceSelect('preview-language', 'Language', namesOf(languages), previewChoice.language);
  const channel = choiceSelect('preview-channel', 'Channel', channelNames, previewChoice.channel);
  const sources = ['draft', 'live'].map((s) => el('label', {},
    el('input', { type: 'radio', name: 'preview-source', value: s, checked: previewChoice.source === s || null }),
    s === 'draft' ? 'Draft (what publishing would ship)' : 'Live release'));
  const result = el('div', { 'aria-live': 'polite' });
  const run = el('button', { type: 'submit', class: 'btn btn-primary' }, 'Resolve');
  const form = el('form', { class: 'card', 'aria-label': 'Preview a session' },
    el('fieldset', { class: 'choice' }, el('legend', {}, 'Resolve against'), el('div', { class: 'radios' }, sources)),
    el('div', { class: 'form-grid' }, tenant.node, agent.node, profile.node, language.node, channel.node),
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
      copy.addEventListener('click', () => navigator.clipboard.writeText(out.configHash).then(() => notify('Config hash copied.'), () => notify('The browser did not allow copying.', 'warn')));
      result.replaceChildren(el('section', { class: 'card', 'aria-label': 'Resolved config' },
        el('h2', {}, out.source === 'live' ? `Resolved from release ${out.release}` : 'Resolved from the draft'),
        el('div', { class: 'hash' }, el('span', { class: 'label' }, 'configHash'), el('code', {}, out.configHash), copy),
        previewSummary(out.config),
        el('pre', { class: 'json', tabindex: '0', 'aria-label': 'Resolved session config' }, prettyJson(out.config))));
    } catch (err) {
      if (err.status !== 401) result.replaceChildren(problemList(err).box);
    } finally {
      run.disabled = false;
    }
  });
  showMain(
    pageHead('Preview', 'Resolve a session config the way the control plane would, and see its hash, before or after publishing.'),
    form, result);
}
