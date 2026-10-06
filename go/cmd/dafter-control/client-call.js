async function startCall() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  const created = await createSession();
  if (created) {
    lobbyForSession(created.room, created.config);
    if (!recordingOf(created.config).enabled) await joinRoom();
  }
  btn.disabled = false;
}

function joinLink(roomId) {
  return `${window.location.origin}/?room=${roomId}`;
}

function copyJoinLink(btn) {
  if (!lastRoomId) return;
  navigator.clipboard.writeText(joinLink(lastRoomId)).then(() => {
    const label = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = label; }, 1500);
  });
}

function setInCall(on) {
  document.body.classList.toggle('in-call', on);
  if (on) renderMediaToggles();
}

function renderToggle(id, name, enabled) {
  const btn = document.getElementById(id);
  btn.textContent = `${name} ${enabled ? 'on' : 'off'}`;
  btn.classList.toggle('off', !enabled);
  btn.setAttribute('aria-pressed', String(enabled));
}

function renderMediaToggles() {
  if (!room) return;
  const local = room.localParticipant;
  renderToggle('btn-mic', 'Mic', local.isMicrophoneEnabled);
  renderToggle('btn-camera', 'Camera', local.isCameraEnabled);
  document.getElementById('btn-camera').style.display = videoEnabled(lastConfig) ? '' : 'none';
}

async function toggleMic() {
  if (!room) return;
  const local = room.localParticipant;
  const turnOn = !local.isMicrophoneEnabled;
  try {
    if (turnOn && !local.getTrackPublication(Track.Source.Microphone)) await publishMicrophone(room);
    else await local.setMicrophoneEnabled(turnOn);
    log(`Microphone ${turnOn ? 'on' : 'off'}`, 'success');
  } catch (err) {
    log(`Could not turn the microphone ${turnOn ? 'on' : 'off'}: ${err.message}`, 'error');
  }
  renderMediaToggles();
}

async function toggleCamera() {
  if (!room) return;
  const local = room.localParticipant;
  const turnOn = !local.isCameraEnabled;
  try {
    await local.setCameraEnabled(turnOn);
    const pub = local.getTrackPublication(Track.Source.Camera);
    if (turnOn && pub && pub.track && !document.getElementById(`tile-${local.identity}-video`)) {
      attachTrack(pub.track, local, true, !syntheticVideo);
    }
    log(`Camera ${turnOn ? 'on' : 'off'}`, 'success');
  } catch (err) {
    log(`Could not turn the camera ${turnOn ? 'on' : 'off'}: ${err.message}`, 'error');
  }
  renderMediaToggles();
}
