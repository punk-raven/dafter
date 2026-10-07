const AVATAR_COLORS = ['#7c3aed', '#2563eb', '#0e7490', '#047857', '#a16207', '#c2410c', '#be185d', '#4338ca'];
const MIC_OFF_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M19 11a1 1 0 0 0-2 0 4.9 4.9 0 0 1-.4 1.9l1.5 1.5A6.9 6.9 0 0 0 19 11zM15 11V5a3 3 0 0 0-5.9-.8L15 10.1zM3.7 2.3 2.3 3.7 9 10.4V11a3 3 0 0 0 4.5 2.6l1.6 1.6A4.9 4.9 0 0 1 7 11a1 1 0 0 0-2 0 7 7 0 0 0 6 6.9V20H8a1 1 0 0 0 0 2h8a1 1 0 0 0 0-2h-3v-2.1a6.9 6.9 0 0 0 3.5-1.4l3.8 3.8 1.4-1.4z"/></svg>';

function personTileId(identity) {
  return `tile-${identity}-video`;
}

function firstLetter(word) {
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    for (const part of new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(word)) return part.segment;
    return '';
  }
  return Array.from(word)[0] || '';
}

function initialsOf(name) {
  const words = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  const letters = words.length > 1 ? firstLetter(words[0]) + firstLetter(words[words.length - 1]) : firstLetter(words[0]);
  return letters.toLocaleUpperCase();
}

function avatarColor(identity) {
  let hash = 0;
  for (const c of String(identity)) hash = (hash * 31 + c.codePointAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function isPerson(participant) {
  return Boolean(participant) && !participant.isAgent && !isPhoneGuest(participant);
}

function tilePart(tag, className, parent) {
  const el = document.createElement(tag);
  el.className = className;
  parent.appendChild(el);
  return el;
}

function personTile(participant) {
  const id = personTileId(participant.identity);
  const existing = document.getElementById(id);
  if (existing) return existing;
  const tile = document.createElement('div');
  tile.id = id;
  tile.className = 'video-tile person-tile';
  tile.dataset.identity = participant.identity;
  tile.dataset.camera = 'off';
  tile.style.setProperty('--avatar-color', avatarColor(participant.identity));
  const card = tilePart('div', 'tile-card', tile);
  tilePart('div', 'tile-avatar', card);
  tilePart('div', 'tile-card-name', card);
  tilePart('span', 'label', tile);
  const mic = tilePart('span', 'tile-mic-off', tile);
  mic.title = 'Microphone off';
  mic.hidden = true;
  mic.innerHTML = MIC_OFF_ICON;
  document.getElementById('video-grid').appendChild(tile);
  nameTile(participant.identity);
  return tile;
}

function nameTile(identity) {
  const tile = document.getElementById(personTileId(identity));
  if (!tile) return;
  const call = currentRoom();
  const local = Boolean(call) && identity === call.localParticipant.identity;
  const label = tileLabel(identity, local);
  tile.querySelector('.tile-avatar').textContent = initialsOf(nameOf(identity));
  tile.querySelector('.tile-card-name').textContent = label;
  tile.querySelector('.label').textContent = label;
}

function cameraOn(participant, tile) {
  const camera = participant.getTrackPublication(Track.Source.Camera);
  return Boolean(camera && camera.track && !camera.isMuted && tile.querySelector('video'));
}

function micOff(participant) {
  const mic = participant.getTrackPublication(Track.Source.Microphone);
  return !mic || mic.isMuted;
}

function renderPerson(participant) {
  if (!isPerson(participant)) return;
  const tile = personTile(participant);
  tile.dataset.camera = cameraOn(participant, tile) ? 'on' : 'off';
  tile.querySelector('.tile-mic-off').hidden = !micOff(participant);
}

function showCamera(track, participant, mirror) {
  const tile = personTile(participant);
  let video = tile.querySelector('video');
  if (video) track.attach(video);
  else {
    video = track.attach();
    tile.insertBefore(video, tile.firstChild);
  }
  video.classList.add('tile-video');
  video.classList.toggle('tile-video-mirrored', mirror);
  renderPerson(participant);
}

function hideCamera(track, participant) {
  track.detach().forEach((el) => el.remove());
  renderPerson(participant);
}

function renderPeople(call) {
  renderPerson(call.localParticipant);
  call.remoteParticipants.forEach(renderPerson);
}

function showSpeakers(speakers) {
  const speaking = new Set(speakers.map((p) => p.identity));
  document.querySelectorAll('#video-grid > .video-tile').forEach((tile) => {
    tile.classList.toggle('speaking', speaking.has(tile.dataset.identity));
  });
}

function watchTiles(call) {
  const rerender = (publication, participant) => renderPerson(participant || call.localParticipant);
  call.on(RoomEvent.Connected, () => renderPeople(call));
  call.on(RoomEvent.ParticipantConnected, renderPerson);
  call.on(RoomEvent.TrackMuted, rerender);
  call.on(RoomEvent.TrackUnmuted, rerender);
  call.on(RoomEvent.TrackPublished, rerender);
  call.on(RoomEvent.TrackUnpublished, rerender);
  call.on(RoomEvent.LocalTrackPublished, () => renderPerson(call.localParticipant));
  call.on(RoomEvent.LocalTrackUnpublished, () => renderPerson(call.localParticipant));
  call.on(RoomEvent.ActiveSpeakersChanged, showSpeakers);
}
