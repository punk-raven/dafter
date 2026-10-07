let recordingSessionId = null;

function showRecordingPanel(data) {
  const rec = (data.config && data.config.recording) || {};
  recordingSessionId = rec.enabled ? data.sessionId : null;
  document.getElementById('panel-recording').style.display = rec.enabled ? 'block' : 'none';
  if (rec.enabled) {
    document.getElementById('recording-status').textContent = `layout ${rec.layout || 'track'} - not started`;
  }
}

function setRecordingStatus(text) {
  document.getElementById('recording-status').textContent = text;
}

function localTrackIds() {
  const ids = {};
  if (!room) return ids;
  const mic = room.localParticipant.getTrackPublication(Track.Source.Microphone);
  const cam = room.localParticipant.getTrackPublication(Track.Source.Camera);
  if (mic) ids.audioTrackId = mic.trackSid;
  if (cam) ids.videoTrackId = cam.trackSid;
  return ids;
}

async function recordingCall(action, body) {
  const resp = await fetch(`/sessions/${recordingSessionId}/recording/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) {
    const detail = (data.details || []).join('; ');
    log(`Recording ${action} failed: ${data.code} - ${data.message}${detail ? ` (${detail})` : ''}`, 'error');
    setRecordingStatus(`${action} refused: ${data.code}`);
    return null;
  }
  return data;
}

async function startRecording() {
  if (!recordingSessionId) return;
  const layout = ((lastConfig && lastConfig.recording) || {}).layout || 'track';
  const ids = localTrackIds();
  let body = {};
  if (layout === 'track_composite') body = ids;
  if (layout === 'track') body = { trackId: ids.audioTrackId || ids.videoTrackId };

  document.getElementById('btn-record-start').disabled = true;
  try {
    const data = await recordingCall('start', body);
    if (!data) return;
    const rec = data.recordings[0];
    log(`Recording started: ${rec.egressId} (${rec.status})`, 'success');
    setRecordingStatus(`${rec.egressId} ${rec.status}`);
  } finally {
    document.getElementById('btn-record-start').disabled = false;
  }
}

async function stopRecording() {
  if (!recordingSessionId) return;
  document.getElementById('btn-record-stop').disabled = true;
  try {
    const data = await recordingCall('stop', {});
    if (!data) return;
    const lines = data.recordings.map((rec) => `${rec.egressId} ${rec.status}`);
    log(`Recording stopped: ${lines.join(', ')}`, 'success');
    setRecordingStatus(lines.join('\n'));
  } finally {
    document.getElementById('btn-record-stop').disabled = false;
  }
}

function attachTrack(track, participant, isLocal = false, mirror = isLocal) {
  if (track.kind === 'video') showCamera(track, participant, mirror);

  if (track.kind === 'audio' && !isLocal) {
    const el = track.attach();
    el.id = `audio-${participant.identity}`;
    document.body.appendChild(el);
    hearFarEnd(track.sid, track.mediaStreamTrack);
  }
}

function detachTrack(track, participant) {
  if (track.kind === 'video') {
    hideCamera(track, participant);
    return;
  }
  track.detach().forEach(el => el.remove());
  if (track.kind === 'audio') forgetFarEnd(track.sid);
}

function removeTile(identity) {
  document.querySelectorAll(`[id^="tile-${identity}"]`).forEach(el => el.remove());
  const audio = document.getElementById(`audio-${identity}`);
  if (audio) audio.remove();
}

async function publishTestPattern(room) {
  const { LocalVideoTrack, LocalAudioTrack } = LivekitClient;

  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 360;
  const ctx = canvas.getContext('2d');
  const colors = ['#e74c3c','#e67e22','#f1c40f','#2ecc71','#3498db','#9b59b6','#1abc9c'];
  const barW = canvas.width / colors.length;

  function drawFrame() {
    for (let i = 0; i < colors.length; i++) {
      ctx.fillStyle = colors[i];
      ctx.fillRect(i * barW, 0, barW, canvas.height);
    }
    const now = new Date();
    const ts = now.toISOString().slice(11, 23);
    ctx.fillStyle = 'rgba(0,0,0,0.6)';
    ctx.fillRect(0, canvas.height - 50, canvas.width, 50);
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 28px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(`Dafter POC  ${ts}`, canvas.width / 2, canvas.height - 16);
    const shift = (Date.now() / 50) % canvas.height;
    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fillRect(0, shift - 5, canvas.width, 10);
  }
  syntheticIntervalId = setInterval(drawFrame, 1000 / 30);
  drawFrame();

  const videoStream = canvas.captureStream(30);
  syntheticVideo = true;
  const videoTrack = new LocalVideoTrack(videoStream.getVideoTracks()[0]);
  await room.localParticipant.publishTrack(videoTrack, { source: Track.Source.Camera });
  log('Synthetic video track published (color bars + timestamp)', 'success');

  syntheticAudioCtx = new AudioContext();
  const audioCtx = syntheticAudioCtx;
  const osc = audioCtx.createOscillator();
  osc.frequency.value = 440;
  const gain = audioCtx.createGain();
  gain.gain.value = 0.05;
  osc.connect(gain);
  const dest = audioCtx.createMediaStreamDestination();
  gain.connect(dest);
  osc.start();

  const audioTrack = new LocalAudioTrack(dest.stream.getAudioTracks()[0]);
  await room.localParticipant.publishTrack(audioTrack, { source: Track.Source.Microphone });
  log('Synthetic audio track published (440Hz tone)', 'success');

  const tile = personTile(room.localParticipant);
  const vid = document.createElement('video');
  vid.className = 'tile-video';
  vid.srcObject = videoStream;
  vid.autoplay = true;
  vid.muted = true;
  tile.insertBefore(vid, tile.firstChild);
  renderPerson(room.localParticipant);
}

async function monitorICE() {
  const infoEl = document.getElementById('ice-info');
  infoEl.style.display = 'block';

  const poll = async () => {
    if (!room || room.state !== ConnectionState.Connected) return;

    document.getElementById('e2ee-state').textContent = room.options.encryption
      ? `isE2EEEnabled=${room.isE2EEEnabled}`
      : (encryptionOf(lastConfig).mode === 'e2ee' ? 'no key: joined without a cryptor' : 'transport only');
    refreshE2EEBadge();

    try {
      const sender = room.engine?.pcManager?.publisher?.pc;
      if (sender) {
        const stats = await sender.getStats();
        stats.forEach(report => {
          if (report.type === 'candidate-pair' && report.state === 'succeeded') {
            document.getElementById('ice-state').textContent = report.state;

            stats.forEach(s => {
              if (s.type === 'local-candidate' && s.id === report.localCandidateId) {
                const ctype = s.candidateType || '-';
                document.getElementById('ice-candidate').textContent = ctype;
                document.getElementById('ice-protocol').textContent = `${s.protocol || '-'} (${s.address || '-'}:${s.port || '-'})`;
                document.getElementById('ice-type').textContent = ctype;
                document.getElementById('ice-turn-status').textContent = ctype === 'relay' ? 'active (relay)' : 'not used (' + ctype + ')';
              }
              if (s.type === 'remote-candidate' && s.id === report.remoteCandidateId) {
                document.getElementById('ice-remote').textContent = `${s.candidateType || '-'} ${s.address || '-'}:${s.port || '-'}`;
              }
            });
          }
        });
      }
    } catch (e) {
    }
    setTimeout(poll, 2000);
  };
  poll();
}

fillNoiseFilterChoices();
fillLlmChoices();
fillSpeechToggles();
