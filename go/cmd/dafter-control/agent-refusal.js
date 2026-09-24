const AGENT_REFUSAL_POLL_MS = 1500;

const agentRefusal = { timer: null, shown: null, warned: false };

function agentRefusalText() {
  const r = agentRefusal.shown;
  if (!r) return '';
  const details = r.details && r.details.length ? ` (${r.details.join('; ')})` : '';
  return `Agent refused: ${r.code} - ${r.message}${details}`;
}

function stopExpectingAgent() {
  clearTimeout(agentRefusal.timer);
  agentRefusal.timer = null;
}

function clearAgentRefusal() {
  stopExpectingAgent();
  agentRefusal.shown = null;
  renderAgentControls();
}

function showAgentRefusal(refusal) {
  stopExpectingAgent();
  agentRefusal.shown = refusal;
  log(agentRefusalText(), 'error');
  setAgentPending(null);
}

function expectAgent(sessionId) {
  clearAgentRefusal();
  agentRefusal.warned = false;
  const started = Date.now();
  const poll = async () => {
    agentRefusal.timer = null;
    if (agentView.sessionId !== sessionId || agentParticipant(agentView.room)) return;
    try {
      const resp = await fetch(`/sessions/${sessionId}`);
      const data = await resp.json();
      if (agentView.sessionId !== sessionId || agentParticipant(agentView.room)) return;
      if (resp.ok && data.agentRefusal) {
        showAgentRefusal(data.agentRefusal);
        return;
      }
    } catch (err) {
      if (!agentRefusal.warned) log(`Could not read the session to check on the agent: ${err.message}`, 'warn');
      agentRefusal.warned = true;
    }
    if (agentView.sessionId === sessionId && Date.now() - started < AGENT_PENDING_MS) {
      agentRefusal.timer = setTimeout(poll, AGENT_REFUSAL_POLL_MS);
    }
  };
  agentRefusal.timer = setTimeout(poll, AGENT_REFUSAL_POLL_MS);
}
