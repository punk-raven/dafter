const ADMIN_TOKEN_KEY = 'dafter.admin.token';
const ADMIN_ACTOR_KEY = 'dafter.admin.actor';
const ADMIN_API_BASE = '/admin/v1';

const ADMIN_KINDS = [
  { kind: 'agents', label: 'Agents', single: 'agent', editable: true },
  { kind: 'profiles', label: 'Profiles', single: 'profile', editable: true },
  { kind: 'tenants', label: 'Tenants', single: 'tenant', editable: true },
  { kind: 'languages', label: 'Languages', single: 'language overlay', editable: true },
  { kind: 'channels', label: 'Channels', single: 'channel overlay', editable: true },
  { kind: 'defaults', label: 'Defaults', single: 'defaults document', editable: false },
  { kind: 'llms', label: 'LLMs', single: 'LLM route', editable: false },
];

function kindInfo(kind) {
  return ADMIN_KINDS.find((k) => k.kind === kind) || null;
}

function sessionValue(key) {
  try {
    return globalThis.sessionStorage.getItem(key) || '';
  } catch {
    return '';
  }
}

function keepSessionValue(key, value) {
  try {
    if (value) globalThis.sessionStorage.setItem(key, value);
    else globalThis.sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

const adminAuth = {
  token: '',
  actor: '',
  restore() {
    this.token = sessionValue(ADMIN_TOKEN_KEY);
    this.actor = sessionValue(ADMIN_ACTOR_KEY);
    return this.token !== '';
  },
  signIn(token, actor) {
    this.token = token.trim();
    this.actor = actor.trim();
    keepSessionValue(ADMIN_TOKEN_KEY, this.token);
    keepSessionValue(ADMIN_ACTOR_KEY, this.actor);
  },
  signOut() {
    this.token = '';
    keepSessionValue(ADMIN_TOKEN_KEY, '');
  },
};

function oneLine(text) {
  return String(text || '').replace(/[\u0000-\u001f\u007f]+/g, ' ').trim();
}

function adminPath(...segments) {
  return ADMIN_API_BASE + segments.map((s) => '/' + encodeURIComponent(s)).join('');
}

function adminRequest(method, path, options = {}) {
  const headers = { Accept: 'application/json', Authorization: 'Bearer ' + (options.token || '') };
  const actor = oneLine(options.actor);
  const note = oneLine(options.note);
  if (actor) headers['X-Dafter-Actor'] = actor;
  if (note) headers['X-Dafter-Note'] = note;
  const request = { method, url: path, headers };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    request.body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
  }
  return request;
}

class AdminError extends Error {
  constructor(status, problem) {
    super(problem.message || `request failed with status ${status}`);
    this.status = status;
    this.code = problem.code || 'internal';
    this.details = Array.isArray(problem.details) ? problem.details : [];
  }
}

function parseDetail(line) {
  const match = /^at '([^']*)':\s*(.*)$/s.exec(String(line));
  if (!match) return { pointer: null, message: String(line) };
  return { pointer: match[1], message: match[2] };
}

function problemOf(status, text) {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && typeof parsed.message === 'string') return parsed;
  } catch {
    return { code: 'internal', message: `the admin API answered ${status} without a JSON error` };
  }
  return { code: 'internal', message: `the admin API answered ${status}` };
}

async function adminCall(method, path, options = {}) {
  const request = adminRequest(method, path, { token: adminAuth.token, actor: adminAuth.actor, ...options });
  let response;
  try {
    response = await fetch(request.url, { method: request.method, headers: request.headers, body: request.body });
  } catch {
    throw new AdminError(0, { code: 'internal', message: 'the admin API could not be reached' });
  }
  const text = await response.text();
  if (!response.ok) {
    const error = new AdminError(response.status, problemOf(response.status, text));
    if (response.status === 401 && typeof onAdminUnauthorized === 'function') onAdminUnauthorized(error);
    throw error;
  }
  return text ? JSON.parse(text) : null;
}

function prettyJson(value) {
  return value === undefined || value === null ? '' : JSON.stringify(value, null, 2);
}

function shortDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
