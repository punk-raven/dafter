const ADMIN_THEME_KEY = 'dafter.admin.theme';
const ADMIN_THEMES = ['auto', 'light', 'dark'];

const adminState = { liveRelease: 0, pending: 0, view: '' };

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'value' && 'value' in node) node.value = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function byId(id) {
  return document.getElementById(id);
}

function showMain(...nodes) {
  byId('main').replaceChildren(...nodes.flat().filter(Boolean));
}

function notify(message, tone = 'ok') {
  const toast = el('div', { class: `toast toast-${tone}`, role: tone === 'error' ? 'alert' : 'status' }, message);
  byId('toasts').append(toast);
  setTimeout(() => toast.remove(), tone === 'error' ? 8000 : 4000);
}

function problemList(error, kind, name, fieldsShown) {
  const placed = placeProblems(kind, name, error.details);
  const box = el('div', { class: 'problems', role: 'alert' },
    el('p', { class: 'problems-title' }, problemTitle(error.message)));
  const all = (error.details || []).map(parseDetail).map((p) => {
    const local = kind ? documentPointer(kind, name, p.pointer) : null;
    return { pointer: local === null ? p.pointer : local || '/', message: p.message };
  });
  const listed = fieldsShown ? placed.general : all;
  if (listed.length) {
    box.append(el('ul', {}, listed.map((p) =>
      el('li', {}, p.pointer !== null ? el('code', {}, p.pointer) : null, p.pointer !== null ? ' ' : null, friendlyMessage(p.message)))));
  }
  if (listed.length < all.length) box.append(el('p', {}, listed.length ? 'The rest are marked next to the fields below.' : 'Each is marked next to its field below.'));
  return { box, placed };
}

function confirmDialog({ title, body, confirmLabel, danger, withNote }) {
  const dialog = byId('confirm');
  const note = el('input', { type: 'text', id: 'confirm-note', maxlength: '500', autocomplete: 'off' });
  const ok = el('button', { type: 'submit', class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, value: 'ok' }, confirmLabel);
  const cancel = el('button', { type: 'button', class: 'btn btn-quiet' }, 'Cancel');
  const form = el('form', { method: 'dialog' },
    el('h2', { id: 'confirm-title' }, title),
    el('p', {}, body),
    withNote ? el('div', { class: 'field' }, el('label', { for: 'confirm-note' }, 'Note (optional)'), note) : null,
    el('div', { class: 'actions' }, cancel, ok));
  dialog.replaceChildren(form);
  dialog.setAttribute('aria-labelledby', 'confirm-title');
  return new Promise((resolve) => {
    cancel.addEventListener('click', () => dialog.close('cancel'));
    dialog.addEventListener('close', () => resolve({ ok: dialog.returnValue === 'ok', note: note.value }), { once: true });
    dialog.returnValue = '';
    dialog.showModal();
    cancel.focus();
  });
}

function currentTheme() {
  try {
    const stored = localStorage.getItem(ADMIN_THEME_KEY);
    return ADMIN_THEMES.includes(stored) ? stored : 'auto';
  } catch {
    return 'auto';
  }
}

function applyTheme(theme) {
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  const button = byId('theme');
  button.textContent = `Theme: ${theme}`;
  button.setAttribute('aria-label', `Colour theme: ${theme}. Change theme`);
}

function cycleTheme() {
  const next = ADMIN_THEMES[(ADMIN_THEMES.indexOf(currentTheme()) + 1) % ADMIN_THEMES.length];
  try {
    localStorage.setItem(ADMIN_THEME_KEY, next);
  } catch {
    notify('This browser does not keep the theme choice; it applies to this page only.', 'warn');
  }
  applyTheme(next);
}

function onAdminUnauthorized() {
  adminAuth.signOut();
  showSignIn('The admin token was not accepted. Enter it again.');
}

function showSignIn(message) {
  document.body.classList.remove('signed-in');
  const token = el('input', { type: 'password', id: 'signin-token', required: '', autocomplete: 'current-password', 'aria-describedby': 'signin-token-hint' });
  const actor = el('input', { type: 'text', id: 'signin-actor', maxlength: '64', autocomplete: 'username', value: adminAuth.actor, 'aria-describedby': 'signin-actor-hint' });
  const error = el('p', { class: 'field-error', role: 'alert', hidden: !message }, message || '');
  const form = el('form', { class: 'card signin', 'aria-labelledby': 'signin-title' },
    el('h1', { id: 'signin-title' }, 'Sign in to the config store'),
    el('div', { class: 'field' }, el('label', { for: 'signin-actor' }, 'Your name'),
      el('p', { class: 'hint', id: 'signin-actor-hint' }, 'Recorded in the change history beside every save, publish and rollback.'), actor),
    el('div', { class: 'field' }, el('label', { for: 'signin-token' }, 'Admin token'),
      el('p', { class: 'hint', id: 'signin-token-hint' }, 'The admin token your team set for this server (DAFTER_ADMIN_TOKEN). It is kept in this browser tab only.'), token),
    error,
    el('div', { class: 'actions' }, el('button', { type: 'submit', class: 'btn btn-primary' }, 'Sign in')));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    adminAuth.signIn(token.value, actor.value);
    try {
      await refreshSummary();
      document.body.classList.add('signed-in');
      route();
    } catch (err) {
      adminAuth.signOut();
      error.textContent = err.status === 401 ? 'That token was not accepted.' : err.message;
      error.hidden = false;
    }
  });
  byId('main').replaceChildren(form);
  (actor.value ? token : actor).focus();
}

async function refreshSummary() {
  const diff = await adminCall('GET', adminPath('diff'));
  adminState.liveRelease = diff.liveRelease;
  adminState.pending = diff.changes.length;
  byId('live-release').textContent = diff.liveRelease ? `Release ${diff.liveRelease} live` : 'Nothing published';
  const badge = byId('pending-count');
  badge.textContent = String(diff.changes.length);
  badge.hidden = diff.changes.length === 0;
  byId('who').textContent = adminAuth.actor || 'admin';
  return diff;
}

const ADMIN_VIEWS = {
  changes: () => showChanges(),
  preview: () => showPreview(),
  releases: () => showReleases(),
  history: () => showHistory(),
};

function parseRoute(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  const [view = 'agents', name = null] = parts;
  if (ADMIN_VIEWS[view]) return { view, name: null };
  if (kindInfo(view)) return { view, name };
  return { view: 'agents', name: null };
}

async function route() {
  if (!adminAuth.token) return showSignIn();
  const { view, name } = parseRoute(location.hash);
  adminState.view = view;
  for (const link of document.querySelectorAll('#nav a')) {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const main = byId('main');
  main.replaceChildren(el('p', { class: 'loading' }, 'Loading…'));
  window.scrollTo(0, 0);
  try {
    if (ADMIN_VIEWS[view]) await ADMIN_VIEWS[view]();
    else if (name === 'new') await showEditor(view, null);
    else if (name) await showEditor(view, name);
    else await showKind(view);
  } catch (err) {
    if (err.status === 401) return;
    main.replaceChildren(el('div', { class: 'card' }, problemList(err).box));
  }
}

function boot() {
  applyTheme(currentTheme());
  byId('theme').addEventListener('click', cycleTheme);
  byId('signout').addEventListener('click', () => {
    adminAuth.signOut();
    showSignIn();
  });
  window.addEventListener('hashchange', route);
  if (adminAuth.restore()) {
    refreshSummary().then(() => {
      document.body.classList.add('signed-in');
      route();
    }, (err) => {
      if (err.status !== 401) showSignIn(err.message);
    });
  } else {
    showSignIn();
  }
}

if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  document.addEventListener('DOMContentLoaded', boot);
}
