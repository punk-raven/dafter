const DIFF_CONTEXT = 3;

function textLines(text) {
  return text ? text.split('\n') : [];
}

function lineDiff(beforeText, afterText) {
  const a = textLines(beforeText);
  const b = textLines(afterText);
  const n = a.length;
  const m = b.length;
  const common = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      common[i][j] = a[i] === b[j] ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      out.push({ op: 'same', text: a[i], before: i + 1, after: j + 1 });
      i++;
      j++;
    } else if (i < n && (j === m || common[i + 1][j] >= common[i][j + 1])) {
      out.push({ op: 'del', text: a[i], before: i + 1 });
      i++;
    } else {
      out.push({ op: 'add', text: b[j], after: j + 1 });
      j++;
    }
  }
  return out;
}

function foldDiff(lines, context = DIFF_CONTEXT) {
  const near = lines.map(() => false);
  lines.forEach((line, k) => {
    if (line.op === 'same') return;
    for (let d = Math.max(0, k - context); d <= Math.min(lines.length - 1, k + context); d++) near[d] = true;
  });
  const out = [];
  let hidden = 0;
  lines.forEach((line, k) => {
    if (line.op !== 'same' || near[k]) {
      if (hidden) out.push({ op: 'fold', count: hidden });
      hidden = 0;
      out.push(line);
    } else {
      hidden++;
    }
  });
  if (hidden) out.push({ op: 'fold', count: hidden });
  return out;
}

function diffStats(lines) {
  return {
    added: lines.filter((l) => l.op === 'add').length,
    removed: lines.filter((l) => l.op === 'del').length,
  };
}

function documentDiff(change) {
  const lines = lineDiff(prettyJson(change.live), prettyJson(change.draft));
  return { lines: foldDiff(lines), stats: diffStats(lines) };
}

const DIFF_MARKS = { same: ' ', add: '+', del: '-' };

function diffRowText(line) {
  if (line.op === 'fold') return `${line.count} unchanged line${line.count === 1 ? '' : 's'}`;
  return DIFF_MARKS[line.op] + ' ' + line.text;
}

function renderDiff(lines) {
  const table = el('div', { class: 'diff', role: 'table', 'aria-label': 'Line changes' });
  for (const line of lines) {
    const row = el('div', { class: `diff-row diff-${line.op}`, role: 'row' });
    if (line.op === 'fold') {
      row.append(el('span', { class: 'diff-fold', role: 'cell' }, diffRowText(line)));
    } else {
      row.append(
        el('span', { class: 'diff-no', role: 'cell', 'aria-hidden': 'true' }, String(line.before || '')),
        el('span', { class: 'diff-no', role: 'cell', 'aria-hidden': 'true' }, String(line.after || '')),
        el('span', { class: 'diff-text', role: 'cell' }, diffRowText(line)),
      );
    }
    table.append(row);
  }
  return table;
}
