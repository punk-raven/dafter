import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.dataset = {};
    this.textContent = '';
    this.hidden = false;
    this.id = '';
    this.className = '';
    this.props = {};
    this.style = { setProperty: (name, value) => { this.props[name] = value; } };
    this.classList = {
      toggle: (name, on) => {
        const names = new Set(this.className.split(' ').filter(Boolean));
        if (on ?? !names.has(name)) names.add(name); else names.delete(name);
        this.className = [...names].join(' ');
      },
      add: (name) => this.classList.toggle(name, true),
      contains: (name) => this.className.split(' ').includes(name),
    };
  }

  appendChild(child) { child.remove(); child.parent = this; this.children.push(child); return child; }

  insertBefore(child, before) {
    child.remove();
    child.parent = this;
    const at = this.children.indexOf(before);
    this.children.splice(at < 0 ? this.children.length : at, 0, child);
    return child;
  }

  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }

  *walk() { for (const child of this.children) { yield child; yield* child.walk(); } }

  matches(selector) {
    if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
    return this.tagName === selector.toUpperCase();
  }

  querySelector(selector) { for (const el of this.walk()) if (el.matches(selector)) return el; return null; }
}

function page() {
  const grid = new FakeElement('div');
  grid.id = 'video-grid';
  const find = (id) => (id === 'video-grid' ? grid : [...grid.walk()].find((el) => el.id === id) || null);
  const document = {
    createElement: (tag) => new FakeElement(tag),
    getElementById: find,
    querySelectorAll: (selector) => {
      assert.equal(selector, '#video-grid > .video-tile');
      return grid.children.filter((el) => el.matches('.video-tile'));
    },
  };
  const run = load('client-tiles.js', 'client-names.js');
  const g = run('globalThis');
  g.document = document;
  g.Track = { Source: { Camera: 'camera', Microphone: 'microphone' } };
  g.isPhoneGuest = (p) => Boolean(p.phone);
  g.room = { localParticipant: { identity: 'p_me' }, remoteParticipants: new Map() };
  const join = (p) => { g.room.remoteParticipants.set(p.identity, p); return p; };
  return { run, grid, find, join };
}

function person(identity, name, { camera = null, mic = 'on' } = {}) {
  const pubs = {
    camera: camera && { track: {}, isMuted: camera === 'muted' },
    microphone: mic && { track: {}, isMuted: mic === 'muted' },
  };
  return { identity, name, isAgent: false, getTrackPublication: (source) => pubs[source] || undefined, pubs };
}

function video() {
  return {
    elements: [],
    attach(el) { const v = el || new FakeElement('video'); this.elements.push(v); return v; },
    detach() { const els = this.elements; this.elements = []; return els; },
  };
}

test('initials are the first letter of the first and last name, in any script', () => {
  const { run } = page();
  const initials = run('initialsOf');
  assert.equal(initials('Rakshan'), 'R');
  assert.equal(initials('allan  smith'), 'AS');
  assert.equal(initials('Asha Rani Kumar'), 'AK');
  assert.equal(initials('निव्या'), 'नि');
  assert.equal(initials('రాకేష్ కుమార్'), 'రాకు');
  assert.equal(initials('   '), '?');
});

test('someone who joins without a camera gets a card with their initials, name and a muted microphone', () => {
  const { run, grid, join } = page();
  const bina = join(person('p_b1', 'Bina Rao', { mic: null }));
  run('renderPerson')(bina);
  const [tile] = grid.children;
  assert.equal(tile.id, 'tile-p_b1-video');
  assert.equal(tile.dataset.camera, 'off');
  assert.equal(tile.querySelector('.tile-avatar').textContent, 'BR');
  assert.equal(tile.querySelector('.tile-card-name').textContent, 'Bina Rao');
  assert.equal(tile.querySelector('.tile-mic-off').hidden, false);
  assert.ok(tile.props['--avatar-color'].startsWith('#'));
});

test('turning the camera on and off swaps card and video inside the same tile', () => {
  const { run, grid } = page();
  const allan = person('p_a1', 'Allan', { camera: 'on' });
  const track = video();
  run('showCamera')(track, allan, false);
  const [tile] = grid.children;
  assert.equal(tile.dataset.camera, 'on');
  assert.ok(tile.querySelector('video').className.includes('tile-video'));
  assert.equal(tile.querySelector('.tile-mic-off').hidden, true);

  allan.pubs.camera.isMuted = true;
  run('renderPerson')(allan);
  assert.equal(tile.dataset.camera, 'off', 'a muted camera shows the card, not a black frame');
  assert.deepEqual(grid.children, [tile]);

  allan.pubs.camera.isMuted = false;
  run('showCamera')(track, allan, false);
  assert.equal(tile.dataset.camera, 'on');
  assert.equal([...tile.walk()].filter((el) => el.tagName === 'VIDEO').length, 1, 'the video element is reused');
  assert.deepEqual(grid.children, [tile]);
});

test('a camera that stops being sent leaves the tile in place with the card', () => {
  const { run, grid } = page();
  const allan = person('p_a1', 'Allan', { camera: 'on' });
  const track = video();
  run('showCamera')(track, allan, false);
  allan.pubs.camera = null;
  run('hideCamera')(track, allan);
  assert.equal(grid.children.length, 1);
  assert.equal(grid.children[0].dataset.camera, 'off');
  assert.equal(grid.children[0].querySelector('video'), null);
});

test('Nivya and phone guests keep their own tiles, and whoever is speaking is highlighted', () => {
  const { run, grid } = page();
  run('renderPerson')({ ...person('agent-AJ_1', 'Nivya'), isAgent: true });
  run('renderPerson')({ ...person('sip_1', 'Phone'), phone: true });
  assert.equal(grid.children.length, 0);

  run('renderPerson')(person('p_a1', 'Allan'));
  const agent = grid.appendChild(new FakeElement('div'));
  agent.className = 'video-tile agent-tile';
  agent.dataset.identity = 'agent-AJ_1';
  const guest = grid.appendChild(new FakeElement('div'));
  guest.className = 'video-tile guest-tile';
  guest.dataset.identity = 'sip_1';

  run('showSpeakers')([{ identity: 'p_a1' }, { identity: 'sip_1' }]);
  assert.deepEqual(grid.children.map((t) => t.classList.contains('speaking')), [true, false, true]);
  run('showSpeakers')([{ identity: 'agent-AJ_1' }]);
  assert.deepEqual(grid.children.map((t) => t.classList.contains('speaking')), [false, true, false]);
});

test('a long caption on a tile keeps only its latest words, so it never spills out of the tile', () => {
  const tail = load('client-captions.js')('captionTail');
  assert.equal(tail('Can you tell me a story?'), 'Can you tell me a story?');
  const story = 'So there was this king named Vikramaditya, right? He was known for being super fair and just. One day, a poor farmer came to him with a really sad story.';
  const shown = tail(story);
  assert.ok(shown.startsWith('…') && story.endsWith(shown.slice(1)), shown);
  assert.ok(shown.length <= 111);
  assert.ok(!shown.slice(1).startsWith(' '));
});
