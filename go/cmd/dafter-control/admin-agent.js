function cleanWords(list) {
  return (list || []).map((w) => String(w).trim()).filter((w) => w !== '');
}

function agentDocumentFrom(values) {
  const doc = { name: String(values.name || '').trim() };
  const aliases = cleanWords(values.aliases);
  const nearMisses = cleanWords(values.nearMisses);
  const profile = String(values.profile || '').trim();
  if (aliases.length) doc.aliases = aliases;
  if (nearMisses.length) doc.nearMisses = nearMisses;
  if (profile) doc.profile = profile;
  return doc;
}

function agentValuesOf(doc) {
  const d = doc && typeof doc === 'object' ? doc : {};
  return {
    name: typeof d.name === 'string' ? d.name : '',
    aliases: Array.isArray(d.aliases) ? d.aliases.map(String) : [],
    nearMisses: Array.isArray(d.nearMisses) ? d.nearMisses.map(String) : [],
    profile: typeof d.profile === 'string' ? d.profile : '',
  };
}

const PREVIOUS_LIMIT = 12;

function previouslyUsed(history, field, current) {
  const taken = new Set(cleanWords(current).map((w) => w.toLowerCase()));
  const out = [];
  for (const doc of [...(history || [])].reverse()) {
    const words = doc && Array.isArray(doc[field]) ? cleanWords(doc[field].map(String)) : [];
    for (const word of words) {
      const key = word.toLowerCase();
      if (taken.has(key)) continue;
      taken.add(key);
      out.push(word);
    }
  }
  return out.slice(0, PREVIOUS_LIMIT);
}

function setInvalid(node, invalid) {
  if (invalid) node.setAttribute('aria-invalid', 'true');
  else node.removeAttribute('aria-invalid');
}

function fieldError(id) {
  return el('p', { class: 'field-error', id, role: 'alert', hidden: '' });
}

function setFieldError(node, messages) {
  node.textContent = (messages || []).map((m) => friendlyMessage(m).replace(/^\w/, (c) => c.toUpperCase())).join(' ');
  node.hidden = !messages || messages.length === 0;
}

function wordList(field, label, hint, words, options = {}) {
  const items = el('ul', { class: 'word-list', 'aria-label': label });
  const error = fieldError(`agent-${field}-error`);
  const addInput = el('input', { type: 'text', id: `agent-${field}-add`, autocomplete: 'off', placeholder: 'Add words, comma separated', 'aria-describedby': `agent-${field}-hint` });
  const addButton = el('button', { type: 'button', class: 'btn btn-quiet' }, 'Add');
  const previous = el('div', { class: 'previous', role: 'group', 'aria-label': `Previously used ${label.toLowerCase()}`, hidden: '' });
  const changed = () => (options.onChange ? options.onChange() : null);
  const currentWords = () => [...items.querySelectorAll('.word input')].map((i) => i.value);

  function row(word) {
    const input = el('input', { type: 'text', value: word, autocomplete: 'off', 'aria-describedby': `agent-${field}-error` });
    const remove = el('button', { type: 'button', class: 'btn btn-icon', 'aria-label': `Remove ${word || 'word'}` }, '×');
    const itemError = el('p', { class: 'field-error', role: 'alert', hidden: '' });
    const li = el('li', { class: 'word' }, el('div', { class: 'word-row' }, input, remove), itemError);
    input.addEventListener('input', () => {
      remove.setAttribute('aria-label', `Remove ${input.value || 'word'}`);
      changed();
    });
    remove.addEventListener('click', () => {
      li.remove();
      addInput.focus();
      changed();
    });
    return li;
  }

  function add() {
    for (const word of cleanWords(addInput.value.split(','))) items.append(row(word));
    addInput.value = '';
    addInput.focus();
    changed();
  }

  function suggest() {
    const others = options.others ? options.others() : [];
    const words = previouslyUsed(options.history, field, currentWords().concat(others));
    previous.replaceChildren(el('span', { class: 'previous-label' }, 'Previously used:'), ...words.map((word) => {
      const chip = el('button', { type: 'button', class: 'chip', 'aria-label': `Add ${word} back to ${label.toLowerCase()}` }, el('span', { 'aria-hidden': 'true' }, '+'), word);
      chip.addEventListener('click', () => {
        items.append(row(word));
        addInput.focus();
        changed();
      });
      return chip;
    }));
    previous.hidden = words.length === 0;
  }
  addButton.addEventListener('click', add);
  addInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      add();
    }
  });
  for (const w of words) items.append(row(w));

  const node = el('div', { class: 'field' },
    el('label', { for: `agent-${field}-add` }, label),
    el('p', { class: 'hint', id: `agent-${field}-hint` }, hint),
    items,
    el('div', { class: 'word-add' }, addInput, addButton),
    previous,
    error);
  return {
    node,
    suggest,
    markClashes(words) {
      const taken = new Set(cleanWords(words).map((w) => w.toLowerCase()));
      for (const input of items.querySelectorAll('.word input')) {
        if (taken.has(input.value.trim().toLowerCase())) input.setAttribute('aria-invalid', 'true');
      }
    },
    read: () => currentWords().concat(addInput.value.split(',')),
    show(fieldMessages, itemMessages) {
      setFieldError(error, fieldMessages);
      let index = 0;
      [...items.children].forEach((li) => {
        const input = li.querySelector('input');
        const messages = input.value.trim() === '' ? undefined : itemMessages[`${field}/${index++}`];
        setFieldError(li.querySelector('.field-error'), messages);
        setInvalid(input, Boolean(messages));
      });
    },
  };
}

function agentForm(doc, profiles, history) {
  const values = agentValuesOf(doc);
  const name = el('input', { type: 'text', id: 'agent-name', required: '', value: values.name, autocomplete: 'off', 'aria-describedby': 'agent-name-hint agent-name-error' });
  const nameError = fieldError('agent-name-error');
  const profile = el('select', { id: 'agent-profile', 'aria-describedby': 'agent-profile-hint agent-profile-error' },
    el('option', { value: '' }, 'None, use the defaults'),
    ...profiles.map((p) => el('option', { value: p }, p)));
  if (values.profile && !profiles.includes(values.profile)) profile.append(el('option', { value: values.profile }, values.profile));
  profile.value = values.profile;
  const profileError = fieldError('agent-profile-error');
  const lists = {};
  const suggestAll = () => Object.values(lists).forEach((list) => list.suggest());
  const aliases = wordList('aliases', 'Aliases', 'Other spellings of the name. Each one wakes the agent, just like the name.', values.aliases,
    { history, onChange: suggestAll, others: () => lists.nearMisses.read() });
  const nearMisses = wordList('nearMisses', 'Near misses', 'Words that sound like the name but must never wake the agent, such as other people\'s names. A word cannot be both an alias and a near miss.', values.nearMisses,
    { history, onChange: suggestAll, others: () => lists.aliases.read() });
  Object.assign(lists, { aliases, nearMisses });
  suggestAll();

  const node = el('div', { class: 'agent-form' },
    el('div', { class: 'field' }, el('label', { for: 'agent-name' }, 'Display name'),
      el('p', { class: 'hint', id: 'agent-name-hint' }, 'The name the agent introduces itself with and wakes up to, e.g. Maya.'), name, nameError),
    aliases.node,
    nearMisses.node,
    el('div', { class: 'field' }, el('label', { for: 'agent-profile' }, 'Profile'),
      el('p', { class: 'hint', id: 'agent-profile-hint' }, 'Settings used when a session asks for this agent without picking a profile itself, such as its persona (how it behaves) and the models it uses.'),
      profile, profileError));

  return {
    node,
    read: () => agentDocumentFrom({ name: name.value, aliases: aliases.read(), nearMisses: nearMisses.read(), profile: profile.value }),
    show(placed) {
      setFieldError(nameError, placed.fields.name);
      setInvalid(name, Boolean(placed.fields.name));
      setFieldError(profileError, placed.fields.profile);
      setInvalid(profile, Boolean(placed.fields.profile));
      aliases.show(placed.fields.aliases, placed.items);
      nearMisses.show(placed.fields.nearMisses, placed.items);
      if (placed.fields.nearMisses) nearMisses.markClashes([name.value, ...aliases.read()]);
    },
  };
}
