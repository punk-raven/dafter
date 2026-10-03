const AGENT_FIELDS = ['name', 'aliases', 'nearMisses', 'profile'];

const RESOLVED_AGENT_FIELDS = {
  '/agent/name': 'name',
  '/agent/addressing/aliases': 'aliases',
  '/agent/addressing/nearMisses': 'nearMisses',
};

function escapePointerToken(token) {
  return String(token).replace(/~/g, '~0').replace(/\//g, '~1');
}

function pointerTokens(pointer) {
  if (!pointer) return [];
  return pointer.split('/').slice(1).map((t) => t.replace(/~1/g, '/').replace(/~0/g, '~'));
}

function documentPointer(kind, name, pointer) {
  if (pointer === null || pointer === undefined) return null;
  const prefix = '/' + escapePointerToken(kind) + '/' + escapePointerToken(name);
  if (pointer === prefix) return '';
  if (pointer.startsWith(prefix + '/')) return pointer.slice(prefix.length);
  return null;
}

function agentFieldOf(kind, name, pointer) {
  if (kind !== 'agents' || !pointer) return null;
  const local = documentPointer(kind, name, pointer);
  let field;
  let rest;
  if (local) {
    const tokens = pointerTokens(local);
    field = tokens[0];
    rest = tokens.slice(1);
  } else {
    const base = Object.keys(RESOLVED_AGENT_FIELDS).find((p) => pointer === p || pointer.startsWith(p + '/'));
    if (!base) return null;
    field = RESOLVED_AGENT_FIELDS[base];
    rest = pointerTokens(pointer.slice(base.length));
  }
  if (!AGENT_FIELDS.includes(field)) return null;
  const index = rest.length && /^\d+$/.test(rest[0]) ? Number(rest[0]) : null;
  return { field, index };
}

function placeProblems(kind, name, details) {
  const placed = { fields: {}, items: {}, general: [] };
  for (const line of details || []) {
    const { pointer, message } = parseDetail(line);
    const target = agentFieldOf(kind, name, pointer);
    if (target && target.index !== null) {
      const key = `${target.field}/${target.index}`;
      (placed.items[key] = placed.items[key] || []).push(message);
      continue;
    }
    if (target) {
      (placed.fields[target.field] = placed.fields[target.field] || []).push(message);
      continue;
    }
    const local = documentPointer(kind, name, pointer);
    const where = local === null ? pointer : local || '/';
    placed.general.push({ pointer: where, message });
  }
  return placed;
}

function friendlyMessage(message) {
  return String(message)
    .replace(/ \(resolving ([^)]*)\)(?: and (\d+) other combination\(s\))?$/, (_, where, more) =>
      `. Found resolving ${where}${more ? ` and ${more} other session combination${more === '1' ? '' : 's'}` : ''}.`)
    .replace(/^false schema\b/, 'is not a field this document accepts')
    .replace(/^minLength: got 0, want 1$/, 'cannot be empty')
    .replace(/^minLength: got (\d+), want (\d+)$/, 'is $1 character(s), needs at least $2')
    .replace(/^maxLength: got (\d+), want (\d+)$/, 'is $1 characters, at most $2 allowed')
    .replace(/^maxItems: got (\d+), want (\d+)$/, 'has $1 entries, at most $2 allowed');
}
