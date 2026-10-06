function sessionRequest() {
  const body = {
    tenantId: document.getElementById('tenant').value,
    language: document.getElementById('language').value,
    channel: document.getElementById('channel').value,
    llm: chosenLlm(),
    device: deviceKey(),
  };
  const profile = document.getElementById('profile').value.trim();
  if (profile) body.profile = profile;
  const agentId = document.getElementById('agent-name').value.trim();
  if (agentId) body.agent = agentId;

  const overrides = {};
  const resolution = document.getElementById('resolution').value;
  if (resolution) overrides.media = { video: { resolution } };
  const noise = document.getElementById('noise-cancellation').value;
  if (noise) overrides.media = Object.assign(overrides.media || {}, { audio: { noiseCancellation: noise } });
  const privacyMode = document.getElementById('privacy-mode').value;
  if (privacyMode) {
    overrides.privacyMode = privacyMode;
    if (privacyMode === 'sealed') {
      overrides.agent = { enabled: false };
      log('sealed refuses an agent at the API, so the override disables it');
    }
  }
  const agent = agentOverride();
  if (agent && !overrides.agent) overrides.agent = agent;
  const addressing = addressingOverride();
  if (addressing && !(overrides.agent && overrides.agent.enabled === false)) {
    overrides.agent = Object.assign(overrides.agent || {}, { addressing });
  }
  speechOverrides(overrides);
  if (Object.keys(overrides).length) body.overrides = overrides;

  const layout = document.getElementById('recording-layout').value;
  if (layout) {
    body.overrides = body.overrides || {};
    body.overrides.recording = { enabled: true, layout, consentArtifactId: 'consent_testclient' };
  }
  const transcription = transcriptionOverride() || (agent && !agent.enabled ? 'off' : null);
  if (transcription) applyTranscriptionOverride(body, transcription);
  if (scribeOverride()) applyScribeOverride(body);
  const phoneGuests = document.getElementById('phone-guests').value;
  if (phoneGuests && body.channel !== 'telephony') {
    body.overrides = body.overrides || {};
    body.overrides.telephony = telephonyOverride(phoneGuests);
  }
  return body;
}

function showCreatedSession(data) {
  lastRoomId = data.room;
  log(`Session created: ${data.sessionId}`, 'success');
  log(`Room: ${data.room} | Hash: ${data.configHash.slice(0, 16)}...`, 'success');
  if (data.agentDispatchId) log(`Agent dispatched to ${data.config.agent.pool} (${data.agentDispatchId}); join to talk to it`, 'success');

  const joinUrl = joinLink(data.room);
  document.getElementById('created-room-id').textContent = data.room;
  document.getElementById('join-link').value = joinUrl;
  document.getElementById('session-created-info').style.display = 'block';
  document.getElementById('room-id').value = data.room;
}

async function createSession() {
  const btn = document.getElementById('btn-create');
  btn.disabled = true;

  try {
    log('Creating session via POST /sessions...');
    const body = sessionRequest();
    const resp = await fetch('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    showResponse(data);

    if (!resp.ok) {
      const details = (data.details || []).join('; ');
      log(`Create failed: ${data.code} - ${data.message}${details ? ` (${details})` : ''}`, 'error');
      document.getElementById('lobby-error').textContent = `Could not start the call: ${details || data.message}`;
      btn.disabled = false;
      return null;
    }

    showCreatedSession(data);
    await allowDialInNumbers(data);
    btn.disabled = false;
    return data;
  } catch (err) {
    log(`Error: ${err.message}`, 'error');
  }
  btn.disabled = false;
  return null;
}

async function joinRoom() {
  const roomId = document.getElementById('room-id').value.trim();
  if (!roomId) {
    log('Enter a Room ID or create a session first', 'error');
    return false;
  }

  const btn = document.getElementById('btn-join');
  btn.disabled = true;

  try {
    log(`Joining room ${roomId}...`);
    const joinBody = { role: document.getElementById('role').value, device: deviceKey() };
    if (lobbyConsent && roomId === lastRoomId) joinBody.recordingConsent = lobbyConsent;
    const resp = await fetch(`/sessions/${roomId}/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(joinBody),
    });
    const data = await resp.json();
    showResponse(data);

    if (!resp.ok) {
      log(`Join failed: ${data.code} - ${data.message}`, 'error');
      btn.disabled = false;
      if (data.code === 'consent_required') await openRoomLobby(roomId);
      else document.getElementById('lobby-error').textContent = 'Could not join this call. Try again in a moment.';
      return false;
    }

    log(`Joined session: ${data.sessionId} | Participant: ${data.participantId}`, 'success');
    localParticipantId = data.participantId;
    lastRoomId = roomId;

    const icePolicy = document.getElementById('ice-policy').value;
    const hasICEServers = data.iceServers && data.iceServers.length > 0;
    if (hasICEServers) {
      log(`TURN credentials received (${data.iceServers[0].urls.length} URLs)`, 'success');
    } else {
      log('No ICE servers in response (TURN not configured)', 'warn');
    }

    setBadge('connecting');

    lastConfig = data.config;
    const publishesVideo = videoEnabled(data.config);
    const roomOptions = applyLocalPreference(roomOptionsFrom(data.config));
    if (data.config && data.config.media) {
      const v = data.config.media.video || {};
      const encryption = encryptionOf(data.config).mode || 'unstated';
      log(`Media profile: ${publishesVideo ? `${v.codec || 'sdk default'} ${v.resolution || ''} ${v.maxBitrate ? `${Math.round(v.maxBitrate / 1000)} kbps` : ''}`.trim() : 'audio only'} · encryption ${encryption} (config ${data.configHash.slice(0, 12)})`, 'success');
    } else {
      log('No media profile in the session document; publishing at the SDK defaults', 'warn');
    }

    const e2ee = await e2eeOptionsFor(data);
    if (e2ee) roomOptions.encryption = e2ee;

    room = new Room(roomOptions);
    watchNames(room);
    watchAgent(room, data);
    watchCaptions(room, data);
    watchScribe(data);
    watchPhone(room, data);
    watchGuests(room, data);

    room.on(RoomEvent.ParticipantEncryptionStatusChanged, (enabled, participant) => {
      const who = participant && participant.identity === room.localParticipant.identity ? 'you' : (participant ? participant.identity : 'unknown');
      log(`Encryption status: ${who} ${enabled ? 'ENCRYPTED' : 'NOT ENCRYPTED'}`, enabled ? 'success' : 'warn');
      refreshE2EEBadge();
    });

    room.on(RoomEvent.EncryptionError, (err) => {
      log(`Encryption error: ${err && err.message ? err.message : err}`, 'error');
    });

    if (e2ee) {
      await e2ee.keyProvider.setKey(base64urlToBytes(data.encryptionKey));
      log('Session key loaded into the key provider');
    }

    room.on(RoomEvent.ConnectionStateChanged, (state) => {
      log(`Connection state: ${state}`);
      setBadge(state === ConnectionState.Connected ? 'connected' : state === ConnectionState.Connecting || state === ConnectionState.Reconnecting ? 'connecting' : 'disconnected');
    });

    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      log(`Subscribed to ${track.kind} from ${participant.identity}`);
      attachTrack(track, participant);
    });

    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      log(`Unsubscribed from ${track.kind} of ${participant.identity}`);
      detachTrack(track, participant);
    });

    room.on(RoomEvent.ParticipantConnected, (participant) => {
      log(`Participant joined: ${participant.identity}`, 'success');
    });

    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      log(`Participant left: ${participant.identity}`, 'warn');
      removeTile(participant.identity);
    });

    room.on(RoomEvent.Disconnected, (reason) => {
      log(`Disconnected: ${reason || 'unknown'}`, 'warn');
      setBadge('disconnected');
      cleanup();
      leftForAnotherTab(reason);
    });

    const rtcConfig = { iceTransportPolicy: icePolicy };
    if (hasICEServers) {
      rtcConfig.iceServers = data.iceServers;
    }

    let connectUrl;
    if (data.url && data.url.startsWith('wss://')) {
      connectUrl = data.url;
    } else if (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') {
      const lkUrl = new URL(data.url);
      lkUrl.hostname = window.location.hostname;
      connectUrl = lkUrl.toString();
    } else {
      const parts = window.location.hostname.split('.');
      parts[0] = 'sfu';
      connectUrl = `wss://${parts.join('.')}`;
    }
    log(`LiveKit endpoint: ${connectUrl} (ICE policy: ${icePolicy})`);

    await room.connect(connectUrl, data.token, { rtcConfig });

    if (e2ee) {
      await room.setE2EEEnabled(true);
      log(`End-to-end encryption enabled before publishing (Room.isE2EEEnabled=${room.isE2EEEnabled})`, 'success');
      refreshE2EEBadge();
    }

    log('Connected! Publishing tracks...', 'success');
    nameJoined(room);

    let micPublished = false;
    try {
      await publishMicrophone(room);
      micPublished = true;
    } catch (micErr) {
      log(`No microphone (${micErr.message})`, publishesVideo ? 'warn' : 'error');
      activeNoiseFilter = null;
    }

    if (publishesVideo) {
      try {
        await room.localParticipant.setCameraEnabled(true);
        log('Camera published', 'success');
      } catch (camErr) {
        log(`No camera (${camErr.message})`, 'warn');
        if (!micPublished) {
          log('No microphone either, publishing synthetic test pattern...', 'warn');
          await publishTestPattern(room);
          activeNoiseFilter = 'none (synthetic audio)';
        }
      }
    } else if (micPublished) {
      log('This channel carries no video', 'success');
    }

    const localVideoTrack = room.localParticipant.getTrackPublication(Track.Source.Camera);
    if (localVideoTrack && localVideoTrack.track) {
      attachTrack(localVideoTrack.track, room.localParticipant, true, !syntheticVideo);
    }

    document.getElementById('panel-create').style.display = 'none';
    document.getElementById('panel-join').style.display = 'none';
    document.getElementById('session-created-info').style.display = 'none';
    setInCall(true);
    showRecordingPanel(data);
    showRecordingIndicator(data.config);

    startStats(data);
    monitorICE();
    return true;
  } catch (err) {
    log(`Error: ${err.message}`, 'error');
    btn.disabled = false;
    setBadge('disconnected');
    return false;
  }
}

async function leaveSession() {
  if (room) {
    await requestMinutes(room);
    await room.disconnect();
  }
  cleanup();
}

function cleanup() {
  stopAgent();
  stopCaptions();
  stopScribe();
  stopPhone();
  stopGuests();
  stopStats();
  syntheticVideo = false;
  if (syntheticIntervalId != null) {
    clearInterval(syntheticIntervalId);
    syntheticIntervalId = null;
  }
  if (syntheticAudioCtx) {
    syntheticAudioCtx.close();
    syntheticAudioCtx = null;
  }
  if (noiseAudioCtx) {
    noiseAudioCtx.close();
    noiseAudioCtx = null;
  }
  activeNoiseFilter = null;
  room = null;
  if (e2eeWorker) {
    e2eeWorker.terminate();
    e2eeWorker = null;
  }
  setE2EEBadge(null);
  document.getElementById('video-grid').innerHTML = '';
  document.getElementById('panel-create').style.display = 'block';
  document.getElementById('panel-join').style.display = 'block';
  document.getElementById('btn-create').disabled = false;
  document.getElementById('btn-join').disabled = false;
  setInCall(false);
  document.getElementById('ice-info').style.display = 'none';
  document.getElementById('panel-recording').style.display = 'none';
  showRecordingIndicator(null);
}
