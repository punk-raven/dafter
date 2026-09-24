const TRANSCRIPTION_TOPIC = 'lk.transcription';
const USER_SILENCE_HANGOVER_MS = 250;
const USER_END_MAX_AGE_MS = 10000;
const BARGE_IN_WINDOW_MS = 3000;

const agentTurns = {
  count: 0,
  totals: [],
  current: null,
  userSpeaking: false,
  userOnsetAt: 0,
  userVoiceAt: 0,
  speakingSince: 0,
  lines: new Map(),
  agentLine: null,
};

function resetAgentTurns() {
  Object.assign(agentTurns, {
    count: 0, totals: [], current: null, userSpeaking: false,
    userOnsetAt: 0, userVoiceAt: 0, speakingSince: 0, lines: new Map(), agentLine: null,
  });
  document.getElementById('agent-lines').innerHTML = '<div class="agent-empty">speech shows here as it is recognised</div>';
  document.getElementById('agent-turn-rows').innerHTML = '<tr class="agent-empty-row"><td colspan="4">no turn yet</td></tr>';
  document.getElementById('agent-p50').textContent = 'p50 -';
}

function ms(v) {
  return v == null ? '-' : `${Math.round(v)} ms`;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function addTurnRow(cells) {
  const body = document.getElementById('agent-turn-rows');
  const empty = body.querySelector('.agent-empty-row');
  if (empty) empty.remove();
  const row = document.createElement('tr');
  row.innerHTML = cells.map((c, i) => `<td class="${i ? 'num' : ''}"></td>`).join('');
  cells.forEach((c, i) => { row.children[i].textContent = c; });
  body.prepend(row);
}

function addBargeRow(stopMs) {
  const body = document.getElementById('agent-turn-rows');
  const empty = body.querySelector('.agent-empty-row');
  if (empty) empty.remove();
  const row = document.createElement('tr');
  row.className = 'agent-barge';
  row.innerHTML = '<td>cut</td><td colspan="3"></td>';
  row.children[1].textContent = `barge-in: agent stopped ${ms(stopMs)} after you started`;
  body.prepend(row);
}

function renderP50() {
  const el = document.getElementById('agent-p50');
  if (!agentTurns.totals.length) {
    el.textContent = 'p50 -';
    return;
  }
  el.textContent = `p50 ${ms(median(agentTurns.totals))} over ${agentTurns.totals.length} turn${agentTurns.totals.length === 1 ? '' : 's'}`;
}

function finishTurn(turn) {
  const endpoint = turn.userEndAt ? turn.thinkingAt - turn.userEndAt : null;
  const respond = turn.firstAudioAt - turn.thinkingAt;
  const total = turn.userEndAt ? turn.firstAudioAt - turn.userEndAt : null;
  if (total != null) agentTurns.totals.push(total);
  addTurnRow([String(turn.n), ms(endpoint), ms(respond), ms(total)]);
  renderP50();
  log(`Turn ${turn.n}: endpoint ${ms(endpoint)}, respond ${ms(respond)}, total ${ms(total)}`, 'success');
}

function agentTurnState(previous, next, now) {
  if (next === 'thinking') {
    const fresh = agentTurns.userVoiceAt && now - agentTurns.userVoiceAt < USER_END_MAX_AGE_MS;
    agentTurns.count += 1;
    agentTurns.current = { n: agentTurns.count, userEndAt: fresh ? agentTurns.userVoiceAt : null, thinkingAt: now };
  }
  if (next === 'speaking') agentTurns.speakingSince = now;
  if (previous === 'speaking' && next !== 'speaking') {
    const onset = agentTurns.userOnsetAt;
    if (onset > agentTurns.speakingSince && now - onset < BARGE_IN_WINDOW_MS) {
      addBargeRow(now - onset);
      log(`Agent interrupted: it stopped speaking ${ms(now - onset)} after you started`, 'warn');
      flashAgentInterrupted();
      markAgentLineInterrupted();
    }
  }
}

function agentTurnLevels(userAudible, agentAudible, now) {
  if (userAudible) {
    if (!agentTurns.userSpeaking) agentTurns.userOnsetAt = now;
    agentTurns.userSpeaking = true;
    agentTurns.userVoiceAt = now;
  } else if (agentTurns.userSpeaking && now - agentTurns.userVoiceAt > USER_SILENCE_HANGOVER_MS) {
    agentTurns.userSpeaking = false;
  }
  const turn = agentTurns.current;
  if (agentAudible && turn && !turn.firstAudioAt) {
    turn.firstAudioAt = now;
    agentTurns.current = null;
    finishTurn(turn);
  }
}

function transcriptSpeaker(room, identity) {
  if (identity === room.localParticipant.identity) return { who: 'you', label: 'You' };
  const p = room.remoteParticipants.get(identity);
  if (p && p.isAgent) return { who: 'agent', label: 'Agent' };
  return { who: 'peer', label: identity };
}

function transcriptLine(segmentId, speaker) {
  let line = agentTurns.lines.get(segmentId);
  if (line) return line;
  const box = document.getElementById('agent-lines');
  const empty = box.querySelector('.agent-empty');
  if (empty) empty.remove();
  line = document.createElement('div');
  line.className = `agent-line agent-line-${speaker.who} agent-interim`;
  line.innerHTML = '<span class="agent-who"></span><span class="agent-text"></span><span class="agent-tag">interim</span>';
  line.querySelector('.agent-who').textContent = speaker.label;
  box.appendChild(line);
  agentTurns.lines.set(segmentId, line);
  return line;
}

function setLineText(line, text, final) {
  const box = document.getElementById('agent-lines');
  const pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  line.querySelector('.agent-text').textContent = text.trim();
  if (final) {
    line.classList.remove('agent-interim');
    if (!line.classList.contains('agent-cut')) line.querySelector('.agent-tag').textContent = 'final';
  }
  if (pinned) box.scrollTop = box.scrollHeight;
}

function markAgentLineInterrupted() {
  const line = agentTurns.agentLine;
  if (!line) return;
  line.classList.add('agent-cut');
  line.querySelector('.agent-tag').textContent = 'interrupted';
}

function watchTranscripts(room) {
  room.registerTextStreamHandler(TRANSCRIPTION_TOPIC, async (reader, participantInfo) => {
    const attrs = reader.info.attributes || {};
    const segmentId = attrs['lk.segment_id'] || reader.info.id;
    const speaker = transcriptSpeaker(room, participantInfo.identity);
    const line = transcriptLine(segmentId, speaker);
    if (speaker.who === 'agent') agentTurns.agentLine = line;
    let text = '';
    try {
      for await (const chunk of reader) {
        text += chunk;
        setLineText(line, text, false);
      }
    } catch (err) {
      log(`transcript stream failed: ${err.message}`, 'warn');
    }
    setLineText(line, text, (reader.info.attributes || {})['lk.transcription_final'] === 'true');
  });
}
