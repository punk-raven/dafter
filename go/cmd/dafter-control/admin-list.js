const SVG_NS = 'http://www.w3.org/2000/svg';

const ICON_PATHS = {
  edit: ['M4 20h4L19 9l-4-4L4 16z', 'M13.5 6.5l4 4'],
  view: ['M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  delete: ['M4 7h16', 'M9 7V4h6v3', 'M6 7l1 13h10l1-13', 'M10 11v6', 'M14 11v6'],
  restore: ['M4 12a8 8 0 1 0 2.4-5.7', 'M4 4v5h5'],
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: '16', height: '16', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false' })) {
    svg.setAttribute(k, v);
  }
  for (const d of ICON_PATHS[name] || []) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

function rowActionsFor(info) {
  return info && info.editable ? ['edit', 'delete'] : ['view'];
}

const ROW_ACTION_LABELS = { edit: 'Edit', view: 'View', delete: 'Delete', restore: 'Restore' };

function rowButton(action, subject, attrs) {
  const label = ROW_ACTION_LABELS[action];
  const tone = action === 'delete' ? 'btn-ghost-danger' : 'btn-quiet';
  const tag = attrs.href ? 'a' : 'button';
  return el(tag, { ...attrs, type: tag === 'button' ? 'button' : null, class: `btn btn-small btn-row ${tone}`, 'aria-label': `${label} ${subject}` },
    icon(action), el('span', { class: 'btn-label' }, label));
}

function docTitle(kind, view) {
  return kind === 'agents' && view.document && view.document.name ? `${view.document.name}` : view.name;
}

function removedStatus(entry) {
  return entry.live ? { label: 'Pending removal', tone: 'removed' } : { label: 'Removed', tone: 'muted' };
}

function removedMeta(entry) {
  const parts = [`deleted ${shortDate(entry.deletedAt)} by ${entry.deletedBy || 'admin'}`, `last revision ${entry.revision}`];
  if (entry.note) parts.push(`"${entry.note}"`);
  return parts.join(' · ');
}

async function deleteFromList(kind, name, problems) {
  const info = kindInfo(kind);
  const answer = await confirmDialog({
    title: `Delete ${info.single} ${name}?`,
    body: 'The deletion is a draft change: sessions keep using it until the next release is published. It stays under Removed, where it can be restored.',
    confirmLabel: 'Delete', danger: true, withNote: true,
  });
  if (!answer.ok) return;
  try {
    await adminCall('DELETE', adminPath(kind, name), { note: answer.note });
    notify(`Deleted ${info.single} ${name} from the draft. Publish it from Changes, or restore it under Removed.`);
    await refreshSummary();
    await showKind(kind);
  } catch (err) {
    if (err.status !== 401) problems.replaceChildren(problemList(err, kind, name).box);
  }
}

async function restoreFromList(kind, name, problems, button) {
  const info = kindInfo(kind);
  button.disabled = true;
  try {
    await adminCall('POST', adminPath(kind, name, 'restore'));
    notify(`Restored ${info.single} ${name} as a draft. Publish it from Changes.`);
    await refreshSummary();
    await showKind(kind);
  } catch (err) {
    button.disabled = false;
    if (err.status !== 401) {
      problems.replaceChildren(problemList(err, kind, name).box);
      problems.scrollIntoView({ block: 'start' });
    }
  }
}

function documentRow(kind, view, problems) {
  const info = kindInfo(kind);
  const status = docStatus(view);
  const title = docTitle(kind, view);
  const subject = `${info.single} ${view.name}`;
  const href = `#/${kind}/${encodeURIComponent(view.name)}`;
  const summary = docSummary(kind, view.document);
  const actions = rowActionsFor(info).map((action) => {
    if (action !== 'delete') return rowButton(action, subject, { href });
    const button = rowButton(action, subject, {});
    button.addEventListener('click', () => deleteFromList(kind, view.name, problems));
    return button;
  });
  return el('li', { class: 'doc-row' },
    el('div', { class: 'doc-main' },
      el('a', { class: 'doc-name', href }, title, title !== view.name ? el('span', { class: 'doc-meta' }, ` (${view.name})`) : null),
      el('div', { class: 'doc-meta' }, `revision ${view.revision}`, summary ? ` · ${summary}` : '')),
    el('span', { class: `pill pill-${status.tone} doc-status` }, status.label),
    el('div', { class: 'row-actions' }, actions));
}

function removedRow(kind, entry, problems) {
  const info = kindInfo(kind);
  const status = removedStatus(entry);
  const title = docTitle(kind, entry);
  const restore = rowButton('restore', `${info.single} ${entry.name}`, {});
  restore.addEventListener('click', () => restoreFromList(kind, entry.name, problems, restore));
  return el('li', { class: 'doc-row' },
    el('div', { class: 'doc-main' },
      el('span', { class: 'doc-name' }, title, title !== entry.name ? el('span', { class: 'doc-meta' }, ` (${entry.name})`) : null),
      el('div', { class: 'doc-meta' }, removedMeta(entry))),
    el('span', { class: `pill pill-${status.tone} doc-status` }, status.label),
    el('div', { class: 'row-actions' }, restore));
}

async function showKind(kind) {
  const info = kindInfo(kind);
  const [views, removed] = await Promise.all([
    adminCall('GET', adminPath(kind)),
    info.editable ? adminCall('GET', adminPath('removed', kind)) : Promise.resolve([]),
  ]);
  const problems = el('div', {});
  const create = info.editable ? el('a', { class: 'btn btn-primary', href: `#/${kind}/new` }, `New ${info.single}`) : null;
  const card = el('section', { class: 'card', 'aria-label': info.label });
  if (views.length === 0) {
    card.append(el('p', { class: 'empty' }, `No ${info.label.toLowerCase()} yet.`));
  } else {
    card.append(el('ul', { class: 'doc-list' }, views.map((v) => documentRow(kind, v, problems))));
  }
  const gone = removed.length === 0 ? null : el('section', { class: 'card', 'aria-labelledby': 'removed-title' },
    el('h2', { id: 'removed-title' }, 'Removed'),
    el('p', { class: 'hint section-lede' }, `${info.label} that were deleted. Restore brings back the last revision as a draft; nothing changes for sessions until you publish.`),
    el('ul', { class: 'doc-list' }, removed.map((entry) => removedRow(kind, entry, problems))));
  showMain(
    pageHead(info.label, KIND_LEDES[kind], create),
    info.editable ? null : el('p', { class: 'note' }, GIT_KIND_NOTE),
    problems,
    card,
    gone);
}
