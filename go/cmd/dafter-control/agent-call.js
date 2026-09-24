const AGENT_LEVEL_INTERVAL_MS = 20;
const AGENT_AUDIBLE_RMS = 0.01;
const USER_AUDIBLE_RMS = 0.02;

const agentMeters = {
  ctx: null,
  timer: null,
  agent: null,
  local: null,
};

function agentTileId(identity) {
  return `tile-${identity}-agent`;
}

function showAgentTile(participant) {
  const id = agentTileId(participant.identity);
  if (document.getElementById(id)) return;
  const tile = document.createElement('div');
  tile.id = id;
  tile.className = 'video-tile agent-tile';
  tile.dataset.state = 'initializing';
  tile.innerHTML = `
    <div class="agent-face"><div class="agent-ring"></div><div class="agent-glyph">AI</div></div>
    <div class="agent-tile-state"><span class="agent-dot"></span><span class="agent-state-text">joining</span></div>
    <button class="agent-unlock" type="button" style="display:none">Click to hear the agent</button>
    <span class="label"></span>`;
  tile.querySelector('.label').textContent = `${participant.name || 'Agent'} · ${participant.identity}`;
  tile.querySelector('.agent-unlock').addEventListener('click', unlockAgentAudio);
  const grid = document.getElementById('video-grid');
  grid.insertBefore(tile, grid.firstChild);
  if (agentView.room) renderAudioUnlock(agentView.room);
}

function hideAgentTile(participant) {
  const tile = document.getElementById(agentTileId(participant.identity));
  if (tile) tile.remove();
  if (agentMeters.agent && agentMeters.agent.identity === participant.identity) {
    agentMeters.agent.source.disconnect();
    agentMeters.agent = null;
  }
}

function setAgentTileState(state) {
  document.querySelectorAll('.agent-tile').forEach((tile) => {
    tile.dataset.state = state || 'initializing';
    tile.querySelector('.agent-state-text').textContent = state || 'joining';
  });
}

function flashAgentInterrupted() {
  document.querySelectorAll('.agent-tile').forEach((tile) => {
    tile.classList.remove('agent-interrupted');
    void tile.offsetWidth;
    tile.classList.add('agent-interrupted');
  });
}

function renderAudioUnlock(room) {
  const blocked = !room.canPlaybackAudio;
  document.querySelectorAll('.agent-unlock').forEach((b) => { b.style.display = blocked ? '' : 'none'; });
  if (blocked) log('The browser blocked audio playback; click the agent tile to hear it', 'warn');
}

async function unlockAgentAudio() {
  if (!agentView.room) return;
  try {
    await agentView.room.startAudio();
    if (agentMeters.ctx && agentMeters.ctx.state !== 'running') await agentMeters.ctx.resume();
    log('Audio playback unlocked', 'success');
  } catch (err) {
    log(`Audio still blocked: ${err.message}`, 'error');
  }
  renderAudioUnlock(agentView.room);
}

function meterContext() {
  if (!agentMeters.ctx) {
    agentMeters.ctx = new AudioContext();
    agentMeters.timer = setInterval(sampleAgentLevels, AGENT_LEVEL_INTERVAL_MS);
  }
  if (agentMeters.ctx.state !== 'running') agentMeters.ctx.resume().catch(() => {});
  return agentMeters.ctx;
}

function meterFor(mediaStreamTrack) {
  const ctx = meterContext();
  const source = ctx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  return { source, analyser, buffer: new Float32Array(analyser.fftSize) };
}

function rms(meter) {
  if (!meter) return 0;
  meter.analyser.getFloatTimeDomainData(meter.buffer);
  let sum = 0;
  for (const v of meter.buffer) sum += v * v;
  return Math.sqrt(sum / meter.buffer.length);
}

function meterAgentAudio(track, participant) {
  showAgentTile(participant);
  if (agentMeters.agent) agentMeters.agent.source.disconnect();
  agentMeters.agent = Object.assign(meterFor(track.mediaStreamTrack), { identity: participant.identity });
  log(`Agent audio attached from ${participant.identity}`, 'success');
}

function meterLocalAudio(track) {
  if (agentMeters.local) agentMeters.local.source.disconnect();
  agentMeters.local = meterFor(track.mediaStreamTrack);
}

function sampleAgentLevels() {
  const now = performance.now();
  const agentLevel = rms(agentMeters.agent);
  const userLevel = rms(agentMeters.local);
  const agentAudible = agentLevel > AGENT_AUDIBLE_RMS;
  if (agentMeters.agent) {
    const tile = document.getElementById(agentTileId(agentMeters.agent.identity));
    if (tile) {
      tile.classList.toggle('agent-audible', agentAudible);
      tile.style.setProperty('--agent-level', Math.min(1, agentLevel * 8).toFixed(3));
    }
  }
  agentTurnLevels(userLevel > USER_AUDIBLE_RMS, agentAudible, now);
}

function stopAgentMeters() {
  if (agentMeters.timer) clearInterval(agentMeters.timer);
  if (agentMeters.ctx) agentMeters.ctx.close().catch(() => {});
  agentMeters.ctx = null;
  agentMeters.timer = null;
  agentMeters.agent = null;
  agentMeters.local = null;
}
