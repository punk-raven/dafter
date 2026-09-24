const AGENT_EVENTS_TOPIC = 'dafter.events';
const AGENT_BADGES = {
  idle: 'badge-disconnected',
  listening: 'badge-connected',
  thinking: 'badge-connecting',
  speaking: 'badge-connected',
};

function agentOverride() {
  const value = document.getElementById('agent-mode').value;
  return value === '' ? null : { enabled: value === 'on' };
}

function setAgentBadge(state) {
  const badge = document.getElementById('agent-badge');
  if (!state) {
    badge.style.display = 'none';
    return;
  }
  badge.style.display = '';
  badge.className = `badge ${AGENT_BADGES[state] || 'badge-disconnected'}`;
  badge.textContent = `agent ${state}`;
}

function watchAgentState(room) {
  setAgentBadge(null);
  room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
    if (topic !== AGENT_EVENTS_TOPIC) return;
    let event;
    try {
      event = JSON.parse(new TextDecoder().decode(payload));
    } catch (err) {
      log(`agent event is not JSON: ${err.message}`, 'warn');
      return;
    }
    if (event.type !== 'agent.state_changed' || !event.payload) return;
    setAgentBadge(event.payload.state);
    log(`agent ${event.payload.previousState || 'joined'} -> ${event.payload.state} (event ${event.sequence})`);
  });
}
