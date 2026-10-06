const NAME_TOPIC = 'dafter.name';
const NAME_KEY = 'dafter.displayName';
const MAX_NAME = 40;

let lobbyConsent = null;
const displayNames = new Map();

function storedName() {
  try {
    return localStorage.getItem(NAME_KEY) || '';
  } catch (err) {
    return '';
  }
}

function rememberName(name) {
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch (err) {
    log(`Your name is not remembered on this device (${err.message})`, 'warn');
  }
}

function cleanName(value) {
  return String(value || '').replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, MAX_NAME);
}

function myName() {
  return cleanName(document.getElementById('display-name').value);
}

function recordingOf(config) {
  return (config && config.recording) || {};
}

function showLobby(view) {
  const recorded = Boolean(view.recorded);
  lastRoomId = view.room || null;
  lobbyConsent = recorded ? view.consentArtifactId : null;
  document.getElementById('lobby-title').textContent = view.room ? 'Join the call' : 'Start a call';
  document.getElementById('recording-notice').hidden = !recorded;
  document.getElementById('lobby-share').hidden = !view.room;
  if (view.room) document.getElementById('lobby-link').value = joinLink(view.room);
  document.getElementById('btn-start').textContent = view.room ? 'Join call' : 'Start a call';
  document.getElementById('lobby-error').textContent = view.error || '';
}

function lobbyForSession(room, config) {
  const rec = recordingOf(config);
  showLobby({ room, recorded: rec.enabled, consentArtifactId: rec.consentArtifactId });
  if (window.history && window.history.replaceState) window.history.replaceState(null, '', `?room=${room}`);
}

async function openRoomLobby(room) {
  document.getElementById('room-id').value = room;
  try {
    const resp = await fetch(`/sessions/${encodeURIComponent(room)}`);
    const data = await resp.json();
    if (!resp.ok) {
      log(`Call link refused: ${data.code} - ${data.message}`, 'error');
      showLobby({ error: 'This call link is not valid. Start a new call instead.' });
      if (window.history && window.history.replaceState) window.history.replaceState(null, '', '/');
      return;
    }
    lobbyForSession(room, data.config);
  } catch (err) {
    showLobby({ room, error: `Could not reach the call: ${err.message}` });
  }
}

async function lobbyAction() {
  const btn = document.getElementById('btn-start');
  rememberName(myName());
  btn.disabled = true;
  try {
    if (lastRoomId) {
      document.getElementById('room-id').value = lastRoomId;
      await joinRoom();
    } else {
      await startCall();
    }
  } finally {
    btn.disabled = false;
  }
}

function showRecordingIndicator(config) {
  const on = Boolean(recordingOf(config).enabled);
  document.getElementById('rec-badge').hidden = !on;
  document.getElementById('rec-banner').hidden = !on;
}

function nameOf(identity) {
  return displayNames.get(identity) || 'Guest';
}

function relabel(identity) {
  const tile = document.getElementById(`tile-${identity}-video`);
  const label = tile && tile.querySelector('.label');
  if (label) label.textContent = tileLabel(identity, room && identity === room.localParticipant.identity);
}

function tileLabel(identity, isLocal) {
  return `${nameOf(identity)}${isLocal ? ' (you)' : ''}`;
}

function announceName(target, to) {
  const name = myName();
  if (!target || !name) return;
  const payload = new TextEncoder().encode(JSON.stringify({ name }));
  const options = { reliable: true, topic: NAME_TOPIC };
  if (to) options.destinationIdentities = to;
  target.localParticipant.publishData(payload, options).catch((err) => log(`Name not shared: ${err.message}`, 'warn'));
}

function watchNames(target) {
  displayNames.clear();
  target.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
    if (topic !== NAME_TOPIC || !participant) return;
    try {
      const name = cleanName(JSON.parse(new TextDecoder().decode(payload)).name);
      if (!name) return;
      displayNames.set(participant.identity, name);
      relabel(participant.identity);
    } catch (err) {
      log(`Unreadable name from ${participant.identity}`, 'warn');
    }
  });
  target.on(RoomEvent.ParticipantConnected, (participant) => announceName(target, [participant.identity]));
}

function nameJoined(target) {
  const name = myName();
  if (name) displayNames.set(target.localParticipant.identity, name);
  relabel(target.localParticipant.identity);
  announceName(target);
}

(function seedLobby() {
  document.getElementById('display-name').value = storedName();
  const roomParam = new URLSearchParams(window.location.search).get('room');
  if (!roomParam) {
    showLobby({});
    return;
  }
  log(`Join link for ${roomParam}`);
  openRoomLobby(roomParam);
})();
