const GIT_KIND_NOTE = 'Kept in git as catalog.json and imported when the control plane starts, so it is read-only here. Change it with a pull request.';

const KIND_LEDES = {
  agents: 'Named agents a session can ask for: the name it answers to, its aliases, the near misses that must never wake it, and its profile.',
  profiles: 'Named bundles of settings a session or an agent picks, such as the persona and the LLM.',
  tenants: 'Per-customer settings: residency, budgets and telephony.',
  languages: 'Overlays and tuning applied when a session runs in a language.',
  channels: 'Overlays and tuning applied per channel: webrtc, telephony and long_form.',
  defaults: 'The baseline every session starts from.',
  llms: 'The LLM routes a session can choose.',
};

function docStatus(view) {
  if (view.published) return { label: 'Live', tone: 'live' };
  if (view.liveRevision) return { label: 'Unpublished changes', tone: 'draft' };
  return { label: 'Not published', tone: 'draft' };
}

function countOf(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function docSummary(kind, doc) {
  const d = doc && typeof doc === 'object' ? doc : {};
  if (kind === 'agents') {
    const parts = [];
    if (d.profile) parts.push(`profile ${d.profile}`);
    parts.push(countOf((d.aliases || []).length, 'alias', 'aliases'));
    parts.push(countOf((d.nearMisses || []).length, 'near miss', 'near misses'));
    return parts.join(' · ');
  }
  return Object.keys(d).join(', ');
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
    el('label', { for: 'doc-key' }, kind === 'agents' ? 'Agent id' : 'Name'),
    el('p', { class: 'hint', id: 'doc-key-hint' }, kind === 'agents' ? 'What a session asks for, e.g. maya. Lowercase letters, digits and hyphens, 2 to 32 characters. It cannot change later.' : 'The document name. It cannot change later.'),
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
        body: 'The deletion is a draft change: sessions keep using it until the next release is published. It stays under Removed, where it can be restored.',
        confirmLabel: 'Delete', danger: true, withNote: true,
      });
      if (!answer.ok) return;
      try {
        await adminCall('DELETE', adminPath(kind, name), { note: answer.note });
        notify(`Deleted ${info.single} ${name} from the draft.`);
        await refreshSummary();
        location.hash = `#/${kind}`;
      } catch (err) {
        if (err.status !== 401) showProblems(err, name);
      }
    });
  }

  const status = view ? docStatus(view) : null;
  const title = isNew ? `New ${info.single}` : (isAgent && doc.name ? `${doc.name} (${name})` : name);
  const body = el('form', { class: 'card', novalidate: '', 'aria-label': title }, tabs, keyField, panel);
  if (info.editable) {
    body.append(el('div', { class: 'field' }, el('label', { for: 'doc-note' }, 'Change note (optional)'), note),
      el('div', { class: 'actions' }, remove, el('a', { class: 'btn btn-quiet', href: `#/${kind}` }, 'Cancel'), save));
    body.addEventListener('submit', submit);
  }
  showMain(
    el('p', { class: 'crumb' }, el('a', { href: `#/${kind}` }, info.label), ' / ', isNew ? 'new' : name),
    el('div', { class: 'page-head' }, el('div', {}, el('h1', {}, title),
      view ? el('p', { class: 'lede' }, `Revision ${view.revision}`, view.liveRevision && !view.published ? `, live is revision ${view.liveRevision}` : '') : null),
    status ? el('span', { class: `pill pill-${status.tone}` }, status.label) : null),
    info.editable ? null : el('p', { class: 'note' }, GIT_KIND_NOTE),
    problems,
    body);
  if (isNew) keyInput.focus();
}
