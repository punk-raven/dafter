const AGENT_EVENTS_TOPIC = 'dafter.events';

const agentView = {
  room: null,
  sessionId: null,
  config: null,
  state: null,
  busy: false,
  pending: null,
  pendingTimer: null,
};

const AGENT_PENDING_MS = 20000;

function agentOverride() {
  const value = document.getElementById('agent-mode').value;
  return value === '' ? null : { enabled: value === 'on' };
}

function agentParticipant(room) {
  if (!room) return null;
  for (const p of room.remoteParticipants.values()) {
    if (p.isAgent) return p;
  }
  return null;
}

function agentPanel() {
  let panel = document.getElementById('agent-panel');
  if (panel) return panel;
  panel = document.createElement('section');
  panel.id = 'agent-panel';
  panel.className = 'agent-panel';
  panel.innerHTML = `
    <div class="agent-bar">
      <strong>Agent</strong>
      <span id="agent-presence" class="agent-pill agent-pill-off">not in call</span>
      <button id="agent-toggle" class="agent-btn" type="button">Invite agent</button>
    </div>
    <div id="agent-reason" class="agent-reason"></div>
    <div class="agent-body">
      <div class="agent-col agent-transcript">
        <div class="agent-col-head"><span>Live transcript</span><span class="agent-hint">interim in italics</span></div>
        <div id="agent-lines" class="agent-lines"><div class="agent-empty">speech shows here as it is recognised</div></div>
      </div>
      <div class="agent-col agent-latency">
        <div class="agent-col-head"><span>Turn latency</span><span id="agent-p50" class="agent-p50">p50 -</span></div>
        <div class="agent-turns-body">
          <table class="agent-turns">
            <thead><tr>
              <th>#</th>
              <th class="num" title="end of your speech to the agent starting to think">endpoint</th>
              <th class="num" title="agent thinking to its first audio heard here">respond</th>
              <th class="num" title="end of your speech to the agent's first audio heard here">total</th>
            </tr></thead>
            <tbody id="agent-turn-rows"><tr class="agent-empty-row"><td colspan="4">no turn yet</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>`;
  const grid = document.getElementById('video-grid');
  const stage = document.createElement('div');
  stage.className = 'agent-stage';
  grid.parentNode.insertBefore(stage, grid);
  stage.append(grid, panel);
  document.getElementById('agent-toggle').addEventListener('click', toggleAgent);
  return panel;
}

function agentBlockedReason(config) {
  if (!config) return 'no session config';
  if (config.privacyMode === 'sealed') return 'sealed sessions never have an agent';
  if (!config.agent || !config.agent.enabled) return 'agent is off in this session\'s config';
  return '';
}

function renderAgentControls() {
  const button = document.getElementById('agent-toggle');
  if (!button) return;
  const reason = agentBlockedReason(agentView.config);
  const present = agentParticipant(agentView.room) !== null;
  const presence = document.getElementById('agent-presence');
  const pending = agentView.pending === (present ? 'leaving' : 'joining') ? agentView.pending : null;
  presence.textContent = pending || (present ? (agentView.state || 'joining') : 'not in call');
  presence.className = `agent-pill agent-pill-${present ? (agentView.state || 'initializing') : (pending ? 'initializing' : 'off')}`;
  document.getElementById('agent-reason').textContent = reason;
  button.textContent = present ? 'Remove agent' : 'Invite agent';
  button.className = `agent-btn ${present ? 'agent-btn-remove' : ''}`;
  button.disabled = agentView.busy || pending !== null || (!present && reason !== '');
  button.title = reason;
}

async function toggleAgent() {
  if (!agentView.sessionId || agentView.busy) return;
  const action = agentParticipant(agentView.room) ? 'stop' : 'start';
  agentView.busy = true;
  renderAgentControls();
  try {
    const resp = await fetch(`/sessions/${agentView.sessionId}/agent/${action}`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) {
      log(`Agent ${action} refused: ${data.code} - ${data.message}${data.details ? ` (${data.details.join('; ')})` : ''}`, 'error');
      return;
    }
    const recalled = data.recalled.length ? `recalled ${data.recalled.join(', ')}` : 'nothing to recall';
    if (action === 'start') log(`Agent invited (${data.agentDispatchId}); ${recalled}`, 'success');
    else log(`Agent removed; ${recalled}`, 'success');
    setAgentPending(action === 'start' ? 'joining' : (data.recalled.length ? 'leaving' : null));
  } catch (err) {
    log(`Agent ${action} failed: ${err.message}`, 'error');
  } finally {
    agentView.busy = false;
    renderAgentControls();
  }
}

function setAgentPending(pending) {
  clearTimeout(agentView.pendingTimer);
  agentView.pending = pending;
  if (pending) {
    agentView.pendingTimer = setTimeout(() => {
      log(`The agent is still not ${pending === 'joining' ? 'in' : 'out of'} the call after ${AGENT_PENDING_MS / 1000}s; check the worker`, 'warn');
      setAgentPending(null);
    }, AGENT_PENDING_MS);
  }
  renderAgentControls();
}

function onAgentEvent(payload) {
  let event;
  try {
    event = JSON.parse(new TextDecoder().decode(payload));
  } catch (err) {
    log(`agent event is not JSON: ${err.message}`, 'warn');
    return;
  }
  if (event.type !== 'agent.state_changed' || !event.payload) return;
  const previous = agentView.state;
  agentView.state = event.payload.state;
  agentTurnState(previous, agentView.state, performance.now());
  setAgentTileState(agentView.state);
  renderAgentControls();
  log(`agent ${event.payload.previousState || 'joined'} -> ${event.payload.state} (event ${event.sequence})`);
}

function watchAgent(room, data) {
  const { RoomEvent } = LivekitClient;
  agentView.room = room;
  agentView.sessionId = data.sessionId;
  agentView.config = data.config;
  agentView.state = null;
  agentPanel().style.display = '';
  resetAgentTurns();
  watchTranscripts(room);

  room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
    if (topic === AGENT_EVENTS_TOPIC) onAgentEvent(payload);
  });
  room.on(RoomEvent.ParticipantConnected, (participant) => {
    if (!participant.isAgent) return;
    agentView.state = null;
    showAgentTile(participant);
    setAgentPending(null);
  });
  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    if (!participant.isAgent) return;
    agentView.state = null;
    hideAgentTile(participant);
    agentTurns.current = null;
    setAgentPending(null);
  });
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    if (participant.isAgent && track.kind === 'audio') meterAgentAudio(track, participant);
  });
  room.on(RoomEvent.LocalTrackPublished, (publication) => {
    if (publication.track && publication.track.kind === 'audio') meterLocalAudio(publication.track);
  });
  room.on(RoomEvent.AudioPlaybackStatusChanged, () => renderAudioUnlock(room));
  room.on(RoomEvent.Connected, () => {
    room.remoteParticipants.forEach((p) => { if (p.isAgent) showAgentTile(p); });
    renderAudioUnlock(room);
    renderAgentControls();
  });
  renderAgentControls();
}

function stopAgent() {
  stopAgentMeters();
  clearTimeout(agentView.pendingTimer);
  agentView.pending = null;
  agentView.room = null;
  agentView.sessionId = null;
  agentView.config = null;
  agentView.state = null;
  const panel = document.getElementById('agent-panel');
  if (panel) panel.style.display = 'none';
}
