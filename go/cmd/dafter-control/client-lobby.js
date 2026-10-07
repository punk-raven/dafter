const NAME_KEY = 'dafter.displayName';
const DEVICE_KEY = 'dafter.device';
const DUPLICATE_IDENTITY = 2;

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
  lastRoomId = view.room || null;
  lobbyConsent = recorded ? view.consentArtifactId : null;
  document.getElementById('lobby-title').textContent = view.room ? 'Join the call' : 'Start a call';
  const [what, detail] = noticeText(Boolean(view.transcribed));
  document.getElementById('notice-what').textContent = what;
  document.getElementById('notice-detail').textContent = detail;
  document.getElementById('recording-notice').hidden = !recorded;
  const language = document.getElementById('language');
  if (view.language) language.value = view.language;
  language.disabled = Boolean(view.room);
  language.title = view.room ? 'The call\'s language is chosen by whoever started it' : '';
  document.getElementById('lobby-create').hidden = Boolean(view.room);
  document.getElementById('lobby-share').hidden = !view.room;
  if (view.room) document.getElementById('lobby-link').value = joinLink(view.room);
  document.getElementById('btn-start').textContent = view.room ? 'Join call' : 'Start a call';
  document.getElementById('lobby-error').textContent = view.error || '';
}

function lobbyForSession(room, config) {
  const rec = recordingOf(config);
  const transcription = (config && config.transcription && config.transcription.mode) || 'off';
  showLobby({
    room, recorded: rec.enabled, consentArtifactId: rec.consentArtifactId,
    transcribed: transcription !== 'off', language: config && config.language,
  });
  if (window.history && window.history.replaceState) window.history.replaceState(null, '', `?room=${room}`);
}

async function openRoomLobby(room) {
  document.getElementById('room-id').value = room;
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
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

function leftForAnotherTab(reason) {
  if (reason !== DUPLICATE_IDENTITY) return false;
  document.getElementById('lobby-error').textContent = 'You joined this call again from another tab or window, so it carries on there.';
  return true;
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
