const GIT_KIND_NOTE = 'Kept in git as catalog.json and imported when the control plane starts, so it is read-only here. Change it with a pull request.';

const KIND_LEDES = {
  agents: 'Named agents a session can ask for: the name it answers to, other spellings of that name, sound-alike words that must never wake it, and its profile.',
  profiles: 'Named bundles of settings a session or an agent picks, such as the persona and the models it uses.',
  tenants: 'Per-customer settings, such as where their data may be processed, how much a session may cost and which phone line they use.',
  languages: 'Settings for each language a session can run in, such as speech-to-text, the voice and how the agent decides the caller has finished speaking.',
  channels: 'Settings for how people join: webrtc for browser and app calls, telephony for phone calls, long_form for long meetings.',
  defaults: 'The baseline every session starts from, before its tenant, profile, language and channel are applied.',
  llms: 'The language models a session can be pointed at, with the provider and the key each one uses.',
};

const AXIS_HINT = 'Tuning is a starting point a session may still change; overlay settings always win, even over what a session asks for.';

const KEY_LABELS = { agents: 'Agent id', tenants: 'Tenant id', languages: 'Language code', channels: 'Channel' };

const KEY_HINTS = {
  agents: 'What a session asks for, e.g. maya. Lowercase letters, digits and hyphens, 2 to 32 characters. It cannot change later.',
  tenants: 'The customer id sessions are created with, e.g. t_9c21a4be. It cannot change later.',
  languages: 'A language code such as hi, en-IN or ta-IN. It cannot change later.',
  channels: 'One of webrtc, telephony or long_form. It cannot change later.',
};

const CHANNEL_LABELS = { webrtc: 'Browser and app calls', telephony: 'Phone calls', long_form: 'Long meetings' };

function languageLabel(tag) {
  try {
    const label = new Intl.DisplayNames(['en'], { type: 'language' }).of(tag);
    return label && label !== tag ? label : '';
  } catch {
    return '';
  }
}

function docTitle(kind, view) {
  const doc = view.document || {};
  if (kind === 'agents' && doc.name) return doc.name;
  if (kind === 'languages') return languageLabel(view.name) || view.name;
  if (kind === 'channels') return CHANNEL_LABELS[view.name] || view.name;
  return view.name;
}

function docStatus(view) {
  if (view.published) return { label: 'Live', tone: 'live' };
  if (view.liveRevision) return { label: 'Unpublished changes', tone: 'draft' };
  return { label: 'Not published', tone: 'draft' };
}

function parseJsonDocument(text) {
  try {
    const doc = JSON.parse(text);
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return { doc: null, error: 'The document must be a JSON object.' };
    return { doc, error: null };
  } catch (err) {
    return { doc: null, error: `Not valid JSON: ${err.message}` };
  }
}

function pageHead(title, lede, ...buttons) {
  const actions = buttons.filter(Boolean);
  return el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, title), lede ? el('p', { class: 'lede' }, lede) : null),
    actions.length ? el('div', { class: 'actions' }, actions) : null);
}

async function agentHistory(name) {
  const revisions = await adminCall('GET', adminPath('agents', name, 'revisions'));
  return revisions.filter((r) => r.document).map((r) => r.document);
}

async function profileNames() {
  const views = await adminCall('GET', adminPath('profiles'));
  return views.map((v) => v.name);
}

function rawEditor(text, readOnly) {
  return el('textarea', { class: 'json', id: 'doc-json', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Document JSON', readonly: readOnly || null, 'aria-describedby': 'doc-json-error' }, text);
}

function editorTabs(onSelect) {
  const form = el('button', { type: 'button', class: 'tab', role: 'tab', id: 'tab-form', 'aria-selected': 'true', 'aria-controls': 'editor-panel' }, 'Form');
  const raw = el('button', { type: 'button', class: 'tab', role: 'tab', id: 'tab-raw', 'aria-selected': 'false', 'aria-controls': 'editor-panel' }, 'Raw JSON');
  const select = (which) => {
    if (!onSelect(which)) return;
    form.setAttribute('aria-selected', String(which === 'form'));
    raw.setAttribute('aria-selected', String(which === 'raw'));
  };
  form.addEventListener('click', () => select('form'));
  raw.addEventListener('click', () => select('raw'));
  return el('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Editor' }, form, raw);
}

function readOnlyBody(kind, doc, title) {
  const rows = glanceRows(kind, doc);
  return el('section', { class: 'card', 'aria-label': title },
    rows.length ? el('dl', { class: 'summary' }, rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)])) : null,
    el('pre', { class: 'json', tabindex: '0', 'aria-label': 'Document JSON' }, prettyJson(doc)),
    el('div', { class: 'actions' }, el('a', { class: 'btn btn-quiet', href: `#/${kind}` }, `Back to ${kindInfo(kind).label}`)));
}

async function showEditor(kind, name) {
  const info = kindInfo(kind);
  const isNew = name === null;
  if (isNew && !info.editable) throw new AdminError(400, { message: `${info.label} live in git and cannot be created here.` });
  const view = isNew ? null : await adminCall('GET', adminPath(kind, name));
  const doc = view ? view.document : (kind === 'agents' ? { name: '' } : {});
  const isAgent = kind === 'agents' && info.editable;
  const [profiles, history] = isAgent ? await Promise.all([profileNames(), isNew ? [] : agentHistory(name)]) : [[], []];

  const problems = el('div', { id: 'doc-problems' });
  const keyInput = el('input', { type: 'text', id: 'doc-key', required: '', autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'doc-key-hint' });
  const keyField = isNew ? el('div', { class: 'field' },
    el('label', { for: 'doc-key' }, KEY_LABELS[kind] || 'Name'),
    el('p', { class: 'hint', id: 'doc-key-hint' }, KEY_HINTS[kind] || 'The name sessions use to pick it. It cannot change later.'),
    keyInput) : null;
  const jsonError = el('p', { class: 'field-error', id: 'doc-json-error', role: 'alert', hidden: '' });
  let form = isAgent ? agentForm(doc, profiles, history) : null;
  let raw = rawEditor(prettyJson(doc) || '{}', !info.editable);
  let mode = isAgent ? 'form' : 'raw';
  const panel = el('div', { id: 'editor-panel', role: isAgent ? 'tabpanel' : null }, isAgent ? form.node : el('div', { class: 'field' }, raw, jsonError));

  function currentDocument() {
    if (mode === 'form') return { doc: form.read(), error: null };
    return parseJsonDocument(raw.value);
  }

  const tabs = isAgent ? editorTabs((which) => {
    if (which === mode) return true;
    if (which === 'raw') {
      raw = rawEditor(prettyJson(form.read()), false);
      panel.replaceChildren(el('div', { class: 'field' }, raw, jsonError));
      jsonError.hidden = true;
    } else {
      const parsed = parseJsonDocument(raw.value);
      if (parsed.error) {
        jsonError.textContent = parsed.error;
        jsonError.hidden = false;
        return false;
      }
      form = agentForm(parsed.doc, profiles, history);
      panel.replaceChildren(form.node);
    }
    mode = which;
    return true;
  }) : null;

  const note = el('input', { type: 'text', id: 'doc-note', maxlength: '500', autocomplete: 'off', placeholder: 'What changed and why' });
  const save = el('button', { type: 'submit', class: 'btn btn-primary' }, isNew ? `Create ${info.single}` : 'Save draft');
  const remove = !isNew && info.editable ? el('button', { type: 'button', class: 'btn btn-ghost-danger spacer' }, 'Delete') : null;

  function showProblems(err, key) {
    const inForm = Boolean(form) && mode === 'form';
    const { box, placed } = problemList(err, kind, key, inForm);
    problems.replaceChildren(box);
    if (inForm) form.show(placed);
    box.scrollIntoView({ block: 'start' });
    const first = inForm ? form.node.querySelector('[aria-invalid]') : null;
    if (first) first.focus({ preventScroll: true });
  }

  async function submit(e) {
    e.preventDefault();
    const key = isNew ? keyInput.value.trim() : name;
    if (!key) {
      setInvalid(keyInput, true);
      keyInput.focus();
      return;
    }
    setInvalid(keyInput, false);
    const parsed = currentDocument();
    if (parsed.error) {
      jsonError.textContent = parsed.error;
      jsonError.hidden = false;
      raw.focus();
      return;
    }
    jsonError.hidden = true;
    save.disabled = true;
    try {
      await adminCall('PUT', adminPath(kind, key), { body: parsed.doc, note: note.value });
      notify(`Saved ${info.single} ${key} to the draft. Publish it from Changes.`);
      await refreshSummary();
      if (isNew) location.hash = `#/${kind}/${encodeURIComponent(key)}`;
      else await showEditor(kind, key);
    } catch (err) {
      if (err.status === 401) return;
      showProblems(err, key);
    } finally {
      save.disabled = false;
    }
  }

  if (remove) {
    remove.addEventListener('click', async () => {
      const answer = await confirmDialog({
        title: `Delete ${info.single} ${name}?`,
        body: deleteBody(view),
        confirmLabel: 'Delete', danger: true, withNote: true,
      });
      if (!answer.ok) return;
      try {
        await adminCall('DELETE', adminPath(kind, name), { note: answer.note });
        notify(deletedMessage(info, view));
        await refreshSummary();
        location.hash = `#/${kind}`;
      } catch (err) {
        if (err.status !== 401) showProblems(err, name);
      }
    });
  }

  const status = view ? docStatus(view) : null;
  const shown = isNew ? '' : docTitle(kind, view);
  const title = isNew ? `New ${info.single}` : (shown !== name ? `${shown} (${name})` : name);
  const axisHint = kind === 'languages' || kind === 'channels' ? el('p', { class: 'hint' }, AXIS_HINT) : null;
  const body = info.editable ? el('form', { class: 'card', novalidate: '', 'aria-label': title }, tabs, keyField, axisHint, panel) : readOnlyBody(kind, doc, title);
  if (info.editable) {
    body.append(el('div', { class: 'field' }, el('label', { for: 'doc-note' }, 'Change note (optional)'), note),
      el('div', { class: 'actions' }, remove, el('a', { class: 'btn btn-quiet', href: `#/${kind}` }, 'Cancel'), save));
    body.addEventListener('submit', submit);
  }
  showMain(
    el('p', { class: 'crumb' }, el('a', { href: `#/${kind}` }, info.label), ' / ', isNew ? 'new' : name),
    el('div', { class: 'page-head' }, el('div', {}, el('h1', {}, title),
      view && updatedLine(view) ? el('p', { class: 'lede' }, updatedLine(view).replace(/^u/, 'U')) : null),
    status ? el('span', { class: `pill pill-${status.tone}` }, status.label) : null),
    info.editable ? null : el('p', { class: 'note' }, GIT_KIND_NOTE),
    problems,
    body);
  if (isNew) keyInput.focus();
}
