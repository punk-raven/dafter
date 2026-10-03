const HISTORY_LIMIT = 200;

const ACTION_LABELS = {
  put: 'Saved',
  delete: 'Deleted',
  restore: 'Restored',
  import: 'Imported',
  seed: 'Seeded',
  publish: 'Published',
  rollback: 'Rolled back',
};

function newestFirst(list, key) {
  return [...list].sort((a, b) => b[key] - a[key]);
}

function historySubject(entry) {
  if (entry.kind && entry.name) {
    const info = kindInfo(entry.kind);
    return { text: `${info ? info.single : entry.kind} ${entry.name}`, href: `#/${entry.kind}/${encodeURIComponent(entry.name)}`, detail: entry.revision ? `revision ${entry.revision}` : '' };
  }
  if (entry.release) return { text: `release ${entry.release}`, href: '#/releases', detail: '' };
  return { text: '', href: null, detail: '' };
}

function historyMatches(entry, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [entry.actor, entry.action, ACTION_LABELS[entry.action], entry.kind, entry.name, entry.note, entry.release && `release ${entry.release}`]
    .some((v) => v && String(v).toLowerCase().includes(q));
}

function releaseOrigin(release) {
  if (release.rolledBackFrom) return `rollback to release ${release.rolledBackFrom}`;
  return 'published from the draft';
}

async function rollbackTo(release) {
  const answer = await confirmDialog({
    title: `Roll back to release ${release.release}?`,
    body: `This publishes a new release with exactly the documents of release ${release.release}. New sessions pick it up within a few seconds. The draft is reset to match, so unpublished edits are replaced.`,
    confirmLabel: 'Roll back', danger: true, withNote: true,
  });
  if (!answer.ok) return;
  try {
    const made = await adminCall('POST', adminPath('releases', String(release.release), 'rollback'), { note: answer.note });
    notify(`Release ${made.release} is live, matching release ${release.release}.`);
    await refreshSummary();
    await showReleases();
  } catch (err) {
    if (err.status !== 401) notify(err.message, 'error');
  }
}

function releaseDocuments(release) {
  const details = el('details', { class: 'release-docs' }, el('summary', {}, 'Documents in this release'));
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    const list = el('ul', {}, el('li', { class: 'loading' }, 'Loading…'));
    details.append(list);
    try {
      const full = await adminCall('GET', adminPath('releases', String(release.release)));
      list.replaceChildren(...(full.revisions || []).map((r) => el('li', {},
        el('a', { href: `#/${r.kind}/${encodeURIComponent(r.name)}` }, `${r.kind}/${r.name}`), ` revision ${r.revision}`)));
    } catch (err) {
      list.replaceChildren(el('li', {}, err.message));
    }
  });
  return details;
}

async function showReleases() {
  const releases = newestFirst(await adminCall('GET', adminPath('releases')), 'release');
  const card = el('section', { class: 'card', 'aria-label': 'Releases' });
  if (releases.length === 0) card.append(el('p', { class: 'empty' }, 'Nothing published yet.'));
  for (const r of releases) {
    const rollback = r.live ? null : el('button', { type: 'button', class: 'btn btn-quiet btn-small' }, 'Roll back to this');
    if (rollback) rollback.addEventListener('click', () => rollbackTo(r));
    card.append(el('div', { class: 'release' },
      el('div', { class: 'release-main' },
        el('div', { class: 'release-title' }, `Release ${r.release}`, r.live ? el('span', { class: 'pill pill-live' }, 'Live') : null),
        el('p', { class: 'release-meta' }, `${shortDate(r.createdAt)} by ${r.actor}, ${releaseOrigin(r)}`),
        r.note ? el('p', {}, r.note) : null,
        releaseDocuments(r)),
      rollback));
  }
  showMain(
    pageHead('Releases', 'Every publish and rollback makes a numbered release. Exactly one is live.', el('a', { class: 'btn btn-quiet', href: '#/history' }, 'History')),
    card);
}

function historyRow(entry) {
  const subject = historySubject(entry);
  return el('li', {},
    el('time', { datetime: entry.at }, shortDate(entry.at)),
    el('span', { class: `pill pill-${entry.action === 'delete' ? 'removed' : entry.action === 'publish' || entry.action === 'rollback' ? 'live' : 'changed'}` }, ACTION_LABELS[entry.action] || entry.action),
    el('div', { class: 'what' },
      subject.href ? el('a', { href: subject.href }, subject.text) : subject.text,
      subject.detail ? el('span', { class: 'by' }, ` ${subject.detail}`) : null,
      el('span', { class: 'by' }, ` by ${entry.actor}`),
      entry.note ? el('p', {}, entry.note) : null));
}

async function showHistory() {
  const entries = await adminCall('GET', adminPath('history') + `?limit=${HISTORY_LIMIT}`);
  const list = el('ol', { class: 'history', 'aria-label': 'Changes, newest first' });
  const shown = el('p', { class: 'hint', 'aria-live': 'polite' });
  const filter = el('input', { type: 'search', id: 'history-filter', autocomplete: 'off', placeholder: 'Name, actor, action or note' });
  const render = () => {
    const rows = entries.filter((e) => historyMatches(e, filter.value));
    list.replaceChildren(...rows.map(historyRow));
    shown.textContent = `Showing ${rows.length} of the latest ${entries.length} changes.`;
  };
  filter.addEventListener('input', render);
  render();
  showMain(
    pageHead('History', 'Who changed what, newest first.'),
    el('section', { class: 'card' },
      el('div', { class: 'field' }, el('label', { for: 'history-filter' }, 'Filter'), filter, shown),
      entries.length ? list : el('p', { class: 'empty' }, 'No changes recorded yet.')));
}
