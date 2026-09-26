const { Room, RoomEvent, Track, ConnectionState, VideoPresets, ExternalE2EEKeyProvider, createLocalAudioTrack } = LivekitClient;

const E2EE_WORKER_URL = 'https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/dist/livekit-client.e2ee.worker.mjs';

let room = null;
let e2eeWorker = null;
let localParticipantId = null;
let syntheticIntervalId = null;
let syntheticVideo = false;
let lastConfig = null;
let syntheticAudioCtx = null;
let lastRoomId = null;
let noiseAudioCtx = null;
let activeNoiseFilter = null;

function log(msg, level = 'info') {
  const el = document.getElementById('log');
  const ts = new Date().toISOString().slice(11, 23);
  el.innerHTML += `<div><span class="ts">${ts}</span><span class="${level}">${escapeHtml(msg)}</span></div>`;
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function setBadge(state) {
  const el = document.getElementById('status-badge');
  el.textContent = state;
  el.className = 'badge badge-' + (state === 'connected' ? 'connected' : state === 'connecting' ? 'connecting' : 'disconnected');
}

function showResponse(data) {
  const box = document.getElementById('session-response');
  box.style.display = 'block';
  box.textContent = JSON.stringify(data, null, 2);
}

function encryptionOf(config) {
  return (config && config.media && config.media.encryption) || {};
}

function setE2EEBadge(state, text) {
  const el = document.getElementById('e2ee-badge');
  if (!state) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'inline';
  el.textContent = text;
  el.className = 'badge badge-e2ee-' + state;
}

function refreshE2EEBadge() {
  if (!room || !room.options.encryption) return;
  if (room.isE2EEEnabled) setE2EEBadge('on', 'E2EE on');
  else setE2EEBadge('pending', 'E2EE pending');
}

function base64urlToBytes(s) {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

async function startE2EEWorker() {
  const resp = await fetch(E2EE_WORKER_URL);
  if (!resp.ok) throw new Error(`E2EE worker fetch failed: HTTP ${resp.status}`);
  const source = await resp.text();
  const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
  return new Worker(url, { type: 'module' });
}

async function e2eeOptionsFor(data) {
  const encryption = encryptionOf(data.config);
  if (encryption.mode !== 'e2ee') return null;
  if (!data.encryptionKey) {
    log('Session is end-to-end encrypted and this join carried no key: remote media will be undecodable', 'error');
    setE2EEBadge('nokey', 'E2EE no key');
    return null;
  }
  e2eeWorker = await startE2EEWorker();
  const keyProvider = new ExternalE2EEKeyProvider();
  log(`Session is end-to-end encrypted (key model ${encryption.keyModel || 'unstated'}); starting the frame cryptor`, 'success');
  setE2EEBadge('pending', 'E2EE pending');
  return { keyProvider, worker: e2eeWorker };
}

const RNNOISE_PKG = '@sapphi-red/web-noise-suppressor@0.4.1';
const RNNOISE_BASE = `https://cdn.jsdelivr.net/npm/${RNNOISE_PKG}/dist`;

const NOISE_FILTERS = {
  off: {
    label: 'off',
    nativeSuppression: false,
    createProcessor: null,
  },
  native: {
    label: 'browser native',
    nativeSuppression: true,
    createProcessor: null,
  },
  rnnoise: {
    label: 'rnnoise (wasm)',
    nativeSuppression: false,
    createProcessor: createRnnoiseProcessor,
  },
};

function fillNoiseFilterChoices() {
  const el = document.getElementById('noise-cancellation');
  for (const [value, entry] of Object.entries(NOISE_FILTERS)) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = `${value} - ${entry.label}`;
    el.appendChild(opt);
  }
}

function noiseFilterFor(value) {
  if (value === undefined) return null;
  return NOISE_FILTERS[value] ||
    { label: `${value} (no entry in this client)`, nativeSuppression: false, createProcessor: null, unknown: true };
}

async function createRnnoiseProcessor() {
  if (typeof AudioWorkletNode === 'undefined') {
    throw new Error('this browser has no AudioWorklet');
  }
  const wns = await import(`${RNNOISE_BASE}/index.js`);
  const wasmBinary = await wns.loadRnnoise({
    url: `${RNNOISE_BASE}/rnnoise.wasm`,
    simdUrl: `${RNNOISE_BASE}/rnnoise_simd.wasm`,
  });

  let source = null;
  let node = null;
  let sink = null;

  return {
    name: 'rnnoise',
    async init(opts) {
      const ctx = opts.audioContext;
      if (!ctx) throw new Error('no audio context on the track');
      await ctx.audioWorklet.addModule(`${RNNOISE_BASE}/rnnoise/workletProcessor.js`);
      source = ctx.createMediaStreamSource(new MediaStream([opts.track]));
      node = new wns.RnnoiseWorkletNode(ctx, { maxChannels: 1, wasmBinary });
      sink = ctx.createMediaStreamDestination();
      source.connect(node);
      node.connect(sink);
      this.processedTrack = sink.stream.getAudioTracks()[0];
    },
    async restart(opts) {
      await this.destroy();
      await this.init(opts);
    },
    async destroy() {
      if (node) { node.destroy(); node.disconnect(); node = null; }
      if (source) { source.disconnect(); source = null; }
      sink = null;
      this.processedTrack = undefined;
    },
  };
}

async function publishMicrophone(room) {
  const capture = Object.assign({}, room.options.audioCaptureDefaults);
  const requested = ((lastConfig && lastConfig.media && lastConfig.media.audio) || {}).noiseCancellation;
  const entry = noiseFilterFor(requested);
  activeNoiseFilter = entry ? entry.label : 'sdk default';

  if (entry && entry.unknown) {
    log(`Profile names noise filter ${requested}, which this client has no entry for; publishing unfiltered`, 'error');
  }
  if (!entry || !entry.createProcessor) {
    await room.localParticipant.setMicrophoneEnabled(true, capture);
    log(`Microphone published; noise filter ${activeNoiseFilter}`, entry && entry.unknown ? 'warn' : 'success');
    return;
  }

  let track = null;
  try {
    const processor = await entry.createProcessor();
    track = await createLocalAudioTrack(capture);
    noiseAudioCtx = new AudioContext({ sampleRate: 48000 });
    await noiseAudioCtx.resume();
    track.setAudioContext(noiseAudioCtx);
    await track.setProcessor(processor);
    if (!track.getProcessor() || !track.getProcessor().processedTrack) {
      throw new Error('processor produced no track');
    }
    await room.localParticipant.publishTrack(track, { source: Track.Source.Microphone });
    log(`Microphone published through ${requested} (${RNNOISE_PKG}); the browser's own suppression is off`, 'success');
  } catch (err) {
    log(`Noise filter ${requested} unavailable (${err.message}); falling back to the browser's own suppression`, 'error');
    if (track) track.stop();
    if (noiseAudioCtx) { noiseAudioCtx.close(); noiseAudioCtx = null; }
    activeNoiseFilter = `${NOISE_FILTERS.native.label} (fallback from ${requested})`;
    await room.localParticipant.setMicrophoneEnabled(
      true, Object.assign({}, capture, { noiseSuppression: true }));
    log(`Microphone published; noise filter ${activeNoiseFilter}`, 'warn');
  }
}

function roomOptionsFrom(config) {
  const media = (config && config.media) || {};
  const video = media.video || {};
  const audio = media.audio || {};
  const options = {};
  const publish = {};

  if (video.adaptiveStream !== undefined) options.adaptiveStream = video.adaptiveStream;
  if (video.dynacast !== undefined) options.dynacast = video.dynacast;

  if (video.codec !== undefined) publish.videoCodec = video.codec;
  if (video.backupCodec !== undefined) publish.backupCodec = { codec: video.backupCodec };
  if (video.scalabilityMode !== undefined) publish.scalabilityMode = video.scalabilityMode;
  if (video.simulcast !== undefined) publish.simulcast = video.simulcast;
  if (audio.red !== undefined) publish.red = audio.red;
  if (audio.dtx !== undefined) publish.dtx = audio.dtx;

  if (video.maxBitrate !== undefined || video.maxFramerate !== undefined) {
    publish.videoEncoding = {};
    if (video.maxBitrate !== undefined) publish.videoEncoding.maxBitrate = video.maxBitrate;
    if (video.maxFramerate !== undefined) publish.videoEncoding.maxFramerate = video.maxFramerate;
  }

  if (video.resolution === 'auto') {
    log('Profile defers resolution to this client; bitrate and framerate ceilings still apply');
  } else {
    const preset = VideoPresets[video.resolution];
    if (preset) options.videoCaptureDefaults = { resolution: preset.resolution };
    else if (video.resolution !== undefined) {
      log(`Profile names resolution ${video.resolution}, which this client SDK has no preset for`, 'warn');
    }
  }

  const capture = {};
  if (audio.echoCancellation !== undefined) capture.echoCancellation = audio.echoCancellation;
  const filter = noiseFilterFor(audio.noiseCancellation);
  if (filter) capture.noiseSuppression = filter.nativeSuppression;
  if (Object.keys(capture).length) options.audioCaptureDefaults = capture;

  if (Object.keys(publish).length) options.publishDefaults = publish;
  return options;
}

function videoEnabled(config) {
  const video = (config && config.media && config.media.video) || {};
  return video.enabled !== false;
}

function myResolution() {
  return document.getElementById('my-resolution').value;
}

function applyLocalPreference(options) {
  const pref = myResolution();
  if (!pref) return options;
  if (pref === 'auto') {
    delete options.videoCaptureDefaults;
    log('Your capture size: browser default, overriding the session profile', 'warn');
  } else if (VideoPresets[pref]) {
    options.videoCaptureDefaults = { resolution: VideoPresets[pref].resolution };
    log(`Your capture size: ${pref}, overriding the session profile`, 'warn');
  }
  return options;
}

async function applyMyResolution() {
  if (statsContext) statsContext.clientResolution = myResolution() || null;
  if (!room) return;

  const pub = room.localParticipant.getTrackPublication(Track.Source.Camera);
  if (!pub || !pub.track) {
    log('No camera track to change', 'warn');
    return;
  }
  if (syntheticVideo) {
    log('The synthetic test pattern is drawn at a fixed size; this applies to a real camera', 'warn');
    return;
  }

  const pref = myResolution();
  try {
    if (!pref) {
      const fromProfile = (lastConfig && lastConfig.media && lastConfig.media.video) || {};
      const preset = VideoPresets[fromProfile.resolution];
      await pub.track.restartTrack(preset ? { resolution: preset.resolution } : {});
      log('Capture size back to the session profile', 'success');
    } else if (pref === 'auto') {
      await pub.track.restartTrack({});
      log('Capture size now the browser default', 'success');
    } else {
      await pub.track.restartTrack({ resolution: VideoPresets[pref].resolution });
      log(`Capture size now ${pref}`, 'success');
    }
  } catch (err) {
    log(`Could not change capture size: ${err.message}`, 'error');
  }
}
