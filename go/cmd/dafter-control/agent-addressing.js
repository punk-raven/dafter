const AGENT_CONTROL_TOPIC = 'dafter.agent';

const agentAddressing = { dormant: null, wokenBy: null, wokenVia: null };

function addressingOverride() {
  const value = document.getElementById('addressing-mode').value;
  return value === '' ? null : { mode: value };
}

function waitsToBeCalled(config) {
  const addressing = config && config.agent && config.agent.addressing;
  return !!addressing && addressing.mode !== 'always';
}

function agentName(config) {
  return (config && config.agent && config.agent.name) || 'the agent';
}

function resetAgentAddressing() {
  agentAddressing.dormant = null;
  agentAddressing.wokenBy = null;
  agentAddressing.wokenVia = null;
}

function whoWokeAgent() {
  const room = agentView.room;
  const id = agentAddressing.wokenBy;
  if (room && room.localParticipant && room.localParticipant.identity === id) return 'you';
  return id;
}

function addressingText(state) {
  if (agentAddressing.dormant === true) return `waiting for its name, ${agentName(agentView.config)}`;
  if (agentAddressing.dormant === false) {
    const how = agentAddressing.wokenVia === 'manual' ? ' with the wake button' : '';
    return `${state || 'awake'} · called by ${whoWokeAgent()}${how}`;
  }
  return state;
}

function onAgentAddressing(payload) {
  const before = `${agentAddressing.dormant}|${agentAddressing.wokenBy}|${agentAddressing.wokenVia}`;
  agentAddressing.dormant = typeof payload.dormant === 'boolean' ? payload.dormant : null;
  agentAddressing.wokenBy = payload.wokenBy || null;
  agentAddressing.wokenVia = payload.wokenVia || null;
  const after = `${agentAddressing.dormant}|${agentAddressing.wokenBy}|${agentAddressing.wokenVia}`;
  if (before === after || agentAddressing.dormant === null) return null;
  if (agentAddressing.dormant) return ['agent went dormant: it answers nobody until its name is said', 'info'];
  const how = agentAddressing.wokenVia === 'manual' ? ' with the wake button' : ' by name';
  return [`agent woke: called by ${whoWokeAgent()}${how}`, 'success'];
}

async function wakeAgent() {
  const room = agentView.room;
  if (!room) return;
  const body = new TextEncoder().encode(JSON.stringify({ action: 'wake' }));
  try {
    await room.localParticipant.publishData(body, { reliable: true, topic: AGENT_CONTROL_TOPIC });
    log('Asked the agent to wake for you, as if you had said its name');
  } catch (err) {
    log(`Wake failed: ${err.message}`, 'error');
  }
}

function renderWakeButton(present) {
  const button = document.getElementById('agent-wake');
  if (!button) return;
  button.style.display = waitsToBeCalled(agentView.config) && !agentQuiet(agentView.config) ? '' : 'none';
  button.textContent = `Wake ${agentName(agentView.config)}`;
  button.disabled = !present;
  button.title = present ? 'Wake the agent as if you had said its name' : 'the agent is not in the call';
}
