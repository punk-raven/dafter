const STATS_INTERVAL_MS = 1000;
const STATS_MAX_SAMPLES = 3600;

let statsTimer = null;
let statsSamples = [];
let statsPrev = new Map();
let statsContext = null;

function startStats(data) {
  stopStats();
  const video = (data.config && data.config.media && data.config.media.video) || {};
  const audio = (data.config && data.config.media && data.config.media.audio) || {};
  statsContext = {
    sessionId: data.sessionId,
    room: data.room,
    participantId: data.participantId,
    configHash: data.configHash,
    profile: { video, audio, encryption: encryptionOf(data.config), noiseFilter: activeNoiseFilter },
    clientResolution: myResolution() || null,
  };
  statsSamples = [];
  statsPrev = new Map();

  const ceiling = [
    video.codec,
    video.resolution,
    video.maxBitrate ? `<=${Math.round(video.maxBitrate / 1000)} kbps` : null,
    video.maxFramerate ? `<=${video.maxFramerate} fps` : null,
    encryptionOf(data.config).mode || null,
    activeNoiseFilter ? `noise ${activeNoiseFilter}` : null,
  ].filter(Boolean).join(' · ');
  const override = statsContext.clientResolution;
  document.getElementById('stats-profile').textContent =
    (ceiling ? `profile: ${ceiling}` : 'profile: none') +
    (override ? `  ·  you chose ${override}` : '');
  document.getElementById('stats-panel').style.display = 'flex';

  statsTimer = setInterval(collectStats, STATS_INTERVAL_MS);
  collectStats();
}

function stopStats() {
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = null;
}

function tracksToSample() {
  const out = [];
  if (!room) return out;
  room.localParticipant.trackPublications.forEach((pub) => {
    if (pub.track) out.push({ dir: 'up', who: 'you', track: pub.track });
  });
  room.remoteParticipants.forEach((p) => {
    p.trackPublications.forEach((pub) => {
      if (pub.track) out.push({ dir: 'down', who: p.identity, track: pub.track });
    });
  });
  return out;
}

async function collectStats() {
  const at = Date.now();
  const rows = [];

  for (const { dir, who, track } of tracksToSample()) {
    let report;
    try {
      report = await track.getRTCStatsReport();
    } catch (err) {
      continue;
    }
    if (!report) continue;

    const byId = new Map();
    report.forEach((s) => byId.set(s.id, s));

    report.forEach((s) => {
      const outbound = s.type === 'outbound-rtp';
      const inbound = s.type === 'inbound-rtp';
      if (!outbound && !inbound) return;

      const key = `${dir}:${who}:${s.id}`;
      const bytes = outbound ? s.bytesSent : s.bytesReceived;
      const prev = statsPrev.get(key);
      let kbps = null;
      if (prev && bytes >= prev.bytes && at > prev.at) {
        kbps = Math.round(((bytes - prev.bytes) * 8) / (at - prev.at));
      }
      statsPrev.set(key, { bytes, at });

      let lost = null;
      if (outbound) {
        report.forEach((r) => {
          if (r.type === 'remote-inbound-rtp' && r.localId === s.id) lost = r.packetsLost;
        });
      } else {
        lost = s.packetsLost;
      }

      const codec = byId.get(s.codecId);
      const source = s.mediaSourceId ? byId.get(s.mediaSourceId) : null;
      const pick = (own, fromSource) => (own != null ? own : (source ? fromSource(source) : null));
      const fps = pick(s.framesPerSecond, (m) => m.framesPerSecond);

      rows.push({
        dir,
        who,
        kind: s.kind || track.kind,
        layer: s.rid || s.scalabilityMode || '',
        codec: codec && codec.mimeType ? codec.mimeType.replace(/^(video|audio)\//, '') : '',
        width: pick(s.frameWidth, (m) => m.width),
        height: pick(s.frameHeight, (m) => m.height),
        fps: fps != null ? Math.round(fps) : null,
        kbps,
        targetKbps: s.targetBitrate ? Math.round(s.targetBitrate / 1000) : null,
        lost,
        active: outbound ? s.active !== false : null,
        limitedBy: s.qualityLimitationReason && s.qualityLimitationReason !== 'none' ? s.qualityLimitationReason : '',
        bytes,
      });
    });
  }

  const sample = { at, iso: new Date(at).toISOString(), rows };
  statsSamples.push(sample);
  if (statsSamples.length > STATS_MAX_SAMPLES) statsSamples.shift();
  renderStats(sample);
}

function renderStats(sample) {
  const tbody = document.getElementById('stats-rows');
  const ceiling = statsContext ? statsContext.profile.video : {};
  const maxKbps = ceiling.maxBitrate ? ceiling.maxBitrate / 1000 : null;
  const maxFps = ceiling.maxFramerate || null;

  if (!sample.rows.length) {
    tbody.innerHTML = '<tr class="empty"><td colspan="11">no streams yet</td></tr>';
  } else {
    tbody.innerHTML = sample.rows.map((r) => {
      const overRate = r.kind === 'video' && maxKbps && r.kbps != null && r.kbps > maxKbps;
      const overFps = r.kind === 'video' && maxFps && r.fps != null && r.fps > maxFps + 1;
      const cell = (v, cls) => `<td class="num ${cls || ''}">${v == null ? '-' : v}</td>`;
      return `<tr class="${r.dir}">
        <td>${r.dir === 'up' ? '&uarr;' : '&darr;'}</td>
        <td>${escapeHtml(r.who)}</td>
        <td>${r.kind}</td>
        <td>${escapeHtml(r.layer)}</td>
        <td>${escapeHtml(r.codec)}</td>
        <td>${r.width ? `${r.width}x${r.height}` : '-'}</td>
        ${cell(r.fps, overFps ? 'over' : '')}
        ${cell(r.kbps, overRate ? 'over' : '')}
        ${cell(r.targetKbps)}
        ${cell(r.lost)}
        <td>${r.active === false ? '<span class="paused">paused (dynacast)</span>' : escapeHtml(r.limitedBy)}</td>
      </tr>`;
    }).join('');
  }

  const sum = (dir) => sample.rows
    .filter((r) => r.dir === dir && r.kbps != null)
    .reduce((t, r) => t + r.kbps, 0);
  const up = sum('up');
  const down = sum('down');

  const upSeries = statsSamples
    .map((s) => s.rows.filter((r) => r.dir === 'up' && r.kbps != null).reduce((t, r) => t + r.kbps, 0))
    .filter((v, i) => i > 0);
  const peak = upSeries.length ? Math.max(...upSeries) : 0;
  const mean = upSeries.length ? Math.round(upSeries.reduce((a, b) => a + b, 0) / upSeries.length) : 0;

  if (statsContext) {
    const ceilingText = document.getElementById('stats-profile').textContent.split('  ·  you chose ')[0];
    document.getElementById('stats-profile').textContent =
      ceilingText + (statsContext.clientResolution ? `  ·  you chose ${statsContext.clientResolution}` : '');
  }
  document.getElementById('stats-totals').textContent =
    `up ${up} kbps (peak ${peak}, mean ${mean}) · down ${down} kbps`;
  document.getElementById('stats-count').textContent = `${statsSamples.length} samples`;
}

function downloadStats(format) {
  if (!statsSamples.length) {
    log('No stats captured yet', 'warn');
    return;
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const base = `dafter-stats-${(statsContext && statsContext.sessionId) || 'session'}-${stamp}`;

  let body, type, name;
  if (format === 'csv') {
    const cols = ['iso', 'dir', 'who', 'kind', 'layer', 'codec', 'width', 'height', 'fps', 'kbps', 'targetKbps', 'lost', 'active', 'limitedBy', 'bytes'];
    const lines = [cols.join(',')];
    for (const s of statsSamples) {
      for (const r of s.rows) {
        lines.push(cols.map((c) => {
          const v = c === 'iso' ? s.iso : r[c];
          if (v == null) return '';
          return /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v;
        }).join(','));
      }
    }
    body = lines.join('\n');
    type = 'text/csv';
    name = `${base}.csv`;
  } else {
    body = JSON.stringify({
      capturedAt: new Date().toISOString(),
      client: 'livekit-client@2.22.3',
      session: statsContext,
      sampleIntervalMs: STATS_INTERVAL_MS,
      samples: statsSamples,
    }, null, 2);
    type = 'application/json';
    name = `${base}.json`;
  }

  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
  log(`Downloaded ${name} (${statsSamples.length} samples)`, 'success');
}
