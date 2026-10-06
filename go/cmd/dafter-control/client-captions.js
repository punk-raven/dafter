const CAPTION_LINGER_MS = 4000;

const TRANSCRIPTION_LABELS = {
  live: 'live captions',
  after_call: 'transcript after the call',
  both: 'live captions + transcript after the call',
};

const captionView = {
  room: null,
  mode: 'off',
  lines: new Map(),
  timers: new Map(),
};

function transcriptionOverride() {
  const mode = document.getElementById('transcription-mode').value;
  return mode === '' ? null : mode;
}

function applyTranscriptionOverride(body, mode) {
  const overrides = body.overrides || {};
  overrides.transcription = { mode };
  if (mode !== 'off') overrides.transcription.consentArtifactId = 'consent_testclient_transcription';
  if ((mode === 'after_call' || mode === 'both') && !overrides.recording) {
    overrides.recording = { enabled: true, layout: 'track', consentArtifactId: 'consent_testclient' };
    log('the transcript after the call is made from each person\'s recorded track, so the override records with the track layout');
  }
  body.overrides = overrides;
}

function transcriptionModeOf(config) {
  return (config && config.transcription && config.transcription.mode) || 'off';
}

function captionsLive(config) {
  const mode = transcriptionModeOf(config);
  return mode === 'live' || mode === 'both';
}

function transcriptionBadge() {
  let badge = document.getElementById('transcription-badge');
  if (badge) return badge;
  badge = document.createElement('span');
  badge.id = 'transcription-badge';
  badge.className = 'badge badge-transcribing';
  badge.style.display = 'none';
  const anchor = document.getElementById('e2ee-badge');
  anchor.parentNode.insertBefore(badge, anchor.nextSibling);
  return badge;
}

function renderTranscriptionBadge(mode) {
  const badge = transcriptionBadge();
  if (mode === 'off') {
    badge.style.display = 'none';
    return;
  }
  badge.textContent = `TRANSCRIBING · ${TRANSCRIPTION_LABELS[mode] || mode}`;
  badge.title = 'Everyone in this call is transcribed by the session\'s provider under its transcription consent';
  badge.style.display = '';
}

function captionPanel() {
  let panel = document.getElementById('caption-panel');
  if (panel) return panel;
  panel = document.createElement('section');
  panel.id = 'caption-panel';
  panel.className = 'caption-panel';
  panel.innerHTML = `
    <div class="caption-head"><strong>Live transcript</strong><span class="caption-hint">everyone, as it is spoken · interim in italics</span></div>
    <div id="caption-lines" class="caption-lines"><div class="caption-empty">what anyone says shows here, labelled by who said it</div></div>`;
  document.getElementById('transcripts').prepend(panel);
  return panel;
}

function captionSpeaker(speaker) {
  if (speaker.kind === 'agent') {
    const agent = agentParticipant(captionView.room);
    return { who: 'agent', label: (agent && agent.name) || 'Agent', identity: null };
  }
  const local = captionView.room && captionView.room.localParticipant.identity === speaker.participantId;
  return { who: local ? 'you' : 'peer', label: local ? 'You' : nameOf(speaker.participantId, captionView.room), identity: speaker.participantId };
}

function captionLine(segmentId, speaker) {
  let line = captionView.lines.get(segmentId);
  if (line) return line;
  const box = document.getElementById('caption-lines');
  const empty = box.querySelector('.caption-empty');
  if (empty) empty.remove();
  line = document.createElement('div');
  line.className = `caption-line caption-line-${speaker.who} caption-interim`;
  line.innerHTML = '<span class="caption-who"></span><span class="caption-text"></span>';
  line.querySelector('.caption-who').textContent = speaker.label;
  line.querySelector('.caption-who').title = speaker.identity || 'the session\'s agent';
  if (speaker.who === 'peer') line.querySelector('.caption-who').dataset.speaker = speaker.identity;
  box.appendChild(line);
  captionView.lines.set(segmentId, line);
  return line;
}

function captionTiles(speaker) {
  if (speaker.who === 'agent') return Array.from(document.querySelectorAll('.agent-tile'));
  const tile = document.getElementById(`tile-${speaker.identity}-video`);
  return tile ? [tile] : [];
}

function showOnTiles(speaker, text, final) {
  const key = speaker.identity || 'agent';
  clearTimeout(captionView.timers.get(key));
  for (const tile of captionTiles(speaker)) {
    let caption = tile.querySelector('.tile-caption');
    if (!caption) {
      caption = document.createElement('div');
      caption.className = 'tile-caption';
      tile.appendChild(caption);
    }
    caption.textContent = text;
    caption.classList.toggle('tile-caption-interim', !final);
  }
  if (final) {
    captionView.timers.set(key, setTimeout(() => {
      for (const tile of captionTiles(speaker)) {
        const caption = tile.querySelector('.tile-caption');
        if (caption) caption.remove();
      }
    }, CAPTION_LINGER_MS));
  }
}

function onCaption(event) {
  if (captionView.mode === 'off') return;
  const { segmentId, speaker, text } = event.payload;
  const final = event.type === 'transcript.final';
  const who = captionSpeaker(speaker);
  const box = document.getElementById('caption-lines');
  const pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  const line = captionLine(segmentId, who);
  line.querySelector('.caption-text').textContent = text;
  line.classList.toggle('caption-interim', !final);
  if (pinned) box.scrollTop = box.scrollHeight;
  showOnTiles(who, text, final);
}

function watchCaptions(room, data) {
  captionView.room = room;
  captionView.mode = transcriptionModeOf(data.config);
  renderTranscriptionBadge(captionView.mode);
  const panel = captionPanel();
  panel.style.display = captionsLive(data.config) ? '' : 'none';
  if (captionView.mode !== 'off') log(`This call is transcribed: ${TRANSCRIPTION_LABELS[captionView.mode]}`, 'warn');
}

function stopCaptions() {
  captionView.timers.forEach((t) => clearTimeout(t));
  captionView.timers.clear();
  captionView.lines.clear();
  captionView.room = null;
  captionView.mode = 'off';
  renderTranscriptionBadge('off');
  const panel = document.getElementById('caption-panel');
  if (panel) panel.remove();
}
