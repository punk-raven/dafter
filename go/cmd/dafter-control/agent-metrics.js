const AGENT_LAYER_COLUMNS = ['endpointMs', 'endOfTurnDelayMs', 'transcriptionDelayMs', 'llmNodeTtftMs', 'ttsNodeTtfbMs', 'e2eLatencyMs'];
const AGENT_LAYER_LABELS = {
  endpointMs: 'endpoint',
  endOfTurnDelayMs: 'end of turn',
  transcriptionDelayMs: 'transcription',
  llmNodeTtftMs: 'LLM first token',
  llmNodeTtfsMs: 'LLM first sentence',
  ttsNodeTtfbMs: 'TTS first byte',
  playbackLatencyMs: 'playback',
  e2eLatencyMs: 'end to end',
  replyGapMs: 'reply gap',
};
const SERIAL_WAIT_MS = 500;
const TURN_COLUMNS = AGENT_LAYER_COLUMNS.length + 2;
const HEARD_COLUMN = TURN_COLUMNS - 1;

function turnCell(v) {
  return v == null ? '-' : String(Math.round(v));
}

function newTurnRow() {
  const body = document.getElementById('agent-turn-rows');
  const empty = body.querySelector('.agent-empty-row');
  if (empty) empty.remove();
  const row = document.createElement('tr');
  row.innerHTML = Array.from({ length: TURN_COLUMNS }, (_, i) => `<td class="${i ? 'num' : ''}">${i ? '-' : ''}</td>`).join('');
  body.prepend(row);
  return row;
}

function turnTitle(row) {
  return [row.dataset.agentDetail, row.dataset.heardDetail].filter(Boolean).join('\n');
}

function addHeardTurn(n, endpoint, respond, total) {
  const row = newTurnRow();
  row.dataset.heard = String(n);
  row.children[0].textContent = `(${n})`;
  row.children[HEARD_COLUMN].textContent = turnCell(total);
  row.dataset.heardDetail = `heard here: endpoint ${ms(endpoint)}, respond ${ms(respond)}, total ${ms(total)}`;
  row.title = turnTitle(row);
}

function onTurnMetrics(payload) {
  const newest = document.querySelector('#agent-turn-rows tr[data-heard]');
  const row = newest && newest.dataset.agent === undefined ? newest : newTurnRow();
  row.dataset.agent = String(payload.turn);
  row.children[0].textContent = String(payload.turn);
  AGENT_LAYER_COLUMNS.forEach((key, i) => { row.children[i + 1].textContent = turnCell(payload[key]); });
  row.classList.toggle('agent-turn-cut', payload.interrupted === true);
  row.classList.toggle('agent-turn-serial', payload.serial === true);
  const layers = Object.entries(AGENT_LAYER_LABELS)
    .filter(([key]) => payload[key] != null)
    .map(([key, label]) => `${label} ${payload[key]} ms`);
  const serial = payload.serial === true
    ? `; went serial, the first sentence reached TTS ${payload.llmNodeTtfsMs - payload.llmNodeTtftMs} ms after the LLM's first token (over ${SERIAL_WAIT_MS} ms) and was under 80% of the reply`
    : '';
  row.dataset.agentDetail = `agent turn ${payload.turn}${payload.interrupted ? ' (interrupted)' : ''}: ${layers.join(', ') || 'no layer measured'}${serial}`;
  row.title = turnTitle(row);
  log(row.dataset.agentDetail);
}

function formatQuantity(item) {
  return Number.isInteger(item.quantity) ? String(item.quantity) : item.quantity.toFixed(1);
}

function resetAgentCost() {
  const el = document.getElementById('agent-cost');
  el.textContent = 'cost -';
  el.title = 'priced from the worker\'s price table; updated after each agent turn';
}

function onSessionUsage(payload) {
  const el = document.getElementById('agent-cost');
  const floor = payload.unpricedItems > 0 ? '>= ' : '';
  const total = `${floor}₹${payload.costInr.toFixed(4)}`;
  el.textContent = payload.final ? `${total} final` : total;
  el.title = payload.items
    .map((i) => `${i.stage} ${i.provider}/${i.model}: ${formatQuantity(i)} ${i.unit} ${i.priced ? `₹${i.costInr.toFixed(4)}` : 'no price'}`)
    .join('\n') || 'no usage yet';
  if (payload.final) {
    const unpriced = payload.unpricedItems ? `, ${payload.unpricedItems} item${payload.unpricedItems === 1 ? '' : 's'} unpriced` : '';
    log(`Session cost ${total}${unpriced}`, 'success');
  }
}
