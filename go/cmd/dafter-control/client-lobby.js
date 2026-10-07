const NAME_KEY = 'dafter.displayName';
const DEVICE_KEY = 'dafter.device';
const LEFT = {
  left: ['You left the call', 'The call is still going. Rejoin it, or start a new call.', 'Rejoin'],
  dropped: ['You were disconnected', 'The call is still going. Rejoin it, or start a new call.', 'Rejoin'],
  moved: ['Moved to another tab', 'You joined this call again from another tab or window, so it carries on there.', 'Continue here'],
};
const LOBBY_HINT = 'Enter your name, then start or join a call';

let lobbyConsent = null;
let pageDevice = null;

function freshDevice() {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_');
}

function deviceKey() {
  try {
    let key = localStorage.getItem(DEVICE_KEY);
    if (!key || !/^[A-Za-z0-9_-]{22,64}$/.test(key)) {
      key = freshDevice();
      localStorage.setItem(DEVICE_KEY, key);
    }
    return key;
  } catch (err) {
    pageDevice = pageDevice || freshDevice();
    return pageDevice;
  }
}

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

function myName() {
  return cleanName(document.getElementById('display-name').value);
}

function recordingOf(config) {
  return (config && config.recording) || {};
}

function noticeText(transcribed) {
  return transcribed
    ? ['This call is recorded and transcribed.', 'Everyone\'s voice and video is recorded and kept, the whole call and each person on their own, and what everyone says is written down as it is spoken.']
    : ['This call is being recorded.', 'Everyone\'s voice and video is recorded and kept, the whole call and each person on their own.'];
}

function showLobby(view) {
  const recorded = Boolean(view.recorded);
  const left = (view.room && LEFT[view.left]) || null;
  lastRoomId = view.room || null;
  lobbyConsent = recorded ? view.consentArtifactId : null;
  document.getElementById('lobby-title').textContent = left ? left[0] : (view.room ? 'Join the call' : 'Start a call');
  const leftLine = document.getElementById('lobby-left');
  leftLine.textContent = left ? left[1] : '';
  leftLine.hidden = !left;
  document.getElementById('lobby-identity').hidden = Boolean(left);
  document.getElementById('lobby-hint').textContent = left ? 'You are not in a call' : LOBBY_HINT;
  document.getElementById('btn-new-call').hidden = !view.room;
  const [what, detail] = noticeText(Boolean(view.transcribed));
  document.getElementById('notice-what').textContent = what;
  document.getElementById('notice-detail').textContent = detail;
  document.getElementById('recording-notice').hidden = !recorded;
  const language = document.getElementById('language');
  if (view.language) language.value = view.language;
  language.disabled = Boolean(view.room);
  language.title = view.room ? 'The call\'s language is chosen by whoever started it' : '';
  document.getElementById('lobby-create').hidden = Boolean(view.room);
  document.getElementById('lobby-share').hidden = !view.room || Boolean(left);
  if (view.room) document.getElementById('lobby-link').value = joinLink(view.room);
  document.getElementById('btn-start').textContent = left ? left[2] : (view.room ? 'Join call' : 'Start a call');
  document.getElementById('lobby-error').textContent = view.error || '';
}

function lobbyForSession(room, config, left = null) {
  const rec = recordingOf(config);
  const transcription = (config && config.transcription && config.transcription.mode) || 'off';
  showLobby({
    room, recorded: rec.enabled, consentArtifactId: rec.consentArtifactId,
    transcribed: transcription !== 'off', language: config && config.language, left,
  });
  if (window.history && window.history.replaceState) window.history.replaceState(null, '', `?room=${room}`);
}

function leaveCallLink(error) {
  showLobby({ error });
  if (window.history && window.history.replaceState) window.history.replaceState(null, '', '/');
}

function showCallEnded() {
  leaveCallLink('This call has ended because everyone left. Start a new call instead.');
}

function startNewCall() {
  leaveCallLink('');
}

async function openRoomLobby(room, left = null) {
  document.getElementById('room-id').value = room;
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  try {
    const resp = await fetch(`/sessions/${encodeURIComponent(room)}`);
    const data = await resp.json();
    if (!resp.ok) {
      log(`Call link refused: ${data.code} - ${data.message}`, 'error');
      leaveCallLink('This call link is not valid. Start a new call instead.');
      return;
    }
    if (data.endedAt) {
      showCallEnded();
      return;
    }
    lobbyForSession(room, data.config, left);
  } catch (err) {
    showLobby({ room, error: `Could not reach the call: ${err.message}` });
  } finally {
    btn.disabled = false;
  }
}

async function lobbyAction() {
  const btn = document.getElementById('btn-start');
  rememberName(myName());
  document.getElementById('lobby-error').textContent = '';
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

function afterCall(room, how) {
  if (room) return openRoomLobby(room, how);
  showLobby({});
  return Promise.resolve();
}

(function seedLobby() {
  document.getElementById('display-name').value = storedName();
  renderDialInFields();
  watchTileLayout();
  const roomParam = new URLSearchParams(window.location.search).get('room');
  if (!roomParam) {
    showLobby({});
    return;
  }
  log(`Join link for ${roomParam}`);
  openRoomLobby(roomParam);
})();
