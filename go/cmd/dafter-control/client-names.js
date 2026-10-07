const NAME_TOPIC = 'dafter.name';
const MAX_NAME = 40;

const displayNames = new Map();

function cleanName(value) {
  return String(value || '').replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, MAX_NAME);
}

function nameOf(identity, call = currentRoom()) {
  if (displayNames.has(identity)) return displayNames.get(identity);
  const participant = call && call.remoteParticipants.get(identity);
  return (participant && participant.name) || 'Guest';
}

function currentRoom() {
  return typeof room === 'undefined' ? null : room;
}

function relabel(identity) {
  nameTile(identity);
  for (const who of document.querySelectorAll('[data-speaker]')) {
    if (who.dataset.speaker === identity) who.textContent = nameOf(identity);
  }
}

function tileLabel(identity, isLocal) {
  return `${nameOf(identity)}${isLocal ? ' (you)' : ''}`;
}

function announceName(target, to, ask = false) {
  const name = myName();
  if (!target || (!name && !ask)) return;
  const message = ask ? { name, ask } : { name };
  const payload = new TextEncoder().encode(JSON.stringify(message));
  const options = { reliable: true, topic: NAME_TOPIC };
  if (to) options.destinationIdentities = to;
  target.localParticipant.publishData(payload, options).catch((err) => log(`Name not shared: ${err.message}`, 'warn'));
}

function watchNames(target) {
  displayNames.clear();
  target.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
    if (topic !== NAME_TOPIC || !participant) return;
    try {
      const message = JSON.parse(new TextDecoder().decode(payload));
      if (message.ask === true) announceName(target, [participant.identity]);
      const name = cleanName(message.name);
      if (!name) return;
      displayNames.set(participant.identity, name);
      relabel(participant.identity);
    } catch (err) {
      log(`Unreadable name from ${participant.identity}`, 'warn');
    }
  });
  target.on(RoomEvent.ParticipantConnected, (participant) => announceName(target, [participant.identity], true));
}

function nameJoined(target) {
  const name = myName();
  if (name) displayNames.set(target.localParticipant.identity, name);
  relabel(target.localParticipant.identity);
  announceName(target, undefined, true);
}
