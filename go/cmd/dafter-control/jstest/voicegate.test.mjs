import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const RATE = 48000;
const BLOCK = 128;

function sine(db, hz, at) {
  const amplitude = 10 ** (db / 20) * Math.SQRT2;
  return Float32Array.from({ length: BLOCK }, (_, i) => amplitude * Math.sin((2 * Math.PI * hz * (at + i)) / RATE));
}

function tone(db, seconds, gate, { her, echo } = {}) {
  const out = new Float32Array(BLOCK);
  const blocks = Math.round((seconds * RATE) / BLOCK);
  for (let b = 0; b < blocks; b++) {
    const at = b * BLOCK;
    const mic = sine(db, 200, at);
    if (echo !== undefined) {
      const back = sine(echo, 330, at);
      for (let i = 0; i < BLOCK; i++) mic[i] += back[i];
    }
    gate.apply(mic, out, her === undefined ? undefined : sine(her, 330, at));
  }
  return gate.gain;
}

const SILENT = -200;

const newGate = () => {
  const run = load('voice-gate.js');
  return new (run('NearVoiceGate'))(RATE);
};

test('a television far below the caller is held back once the caller has spoken', () => {
  const gate = newGate();
  assert.ok(tone(-38, 2, gate) > 0.9, 'before the caller speaks there is nothing to compare with, so it passes');
  assert.ok(tone(-16, 8, gate) > 0.9, 'the caller passes');
  assert.ok(tone(-38, 3, gate) < 0.05, 'the television 22 dB below the caller is held back');
  assert.ok(tone(-38, 120, gate, { her: -20 }) < 0.05, 'and stays held back through two minutes of the agent talking');
});

test('the caller speaking softly still passes, and a short bang does not lock them out', () => {
  const gate = newGate();
  tone(-16, 8, gate);
  assert.ok(tone(-26, 1, gate) > 0.9, 'a softer stretch 10 dB below the caller still passes');
  tone(-2, 0.05, gate);
  assert.ok(tone(-20, 1, gate) > 0.9, 'a 50 ms bang does not raise the bar over the caller');
});

test('silence and hiss below the floor are held back', () => {
  const gate = newGate();
  tone(-16, 8, gate);
  assert.ok(tone(-70, 2, gate) < 0.05);
});

test('the television speaking up as soon as the caller stops is held back', () => {
  const gate = newGate();
  tone(-16, 8, gate);
  tone(-30, 0.4, gate);
  assert.ok(tone(-30, 0.4, gate) < 0.05, 'a voice 14 dB below the caller is held back within half a second of the caller stopping');
});

test('a caller who raised their voice is heard again at their normal level', () => {
  const gate = newGate();
  tone(-30, 6, gate);
  assert.ok(tone(-8, 2, gate) > 0.9, 'the raised voice passes');
  assert.ok(tone(-30, 1, gate) > 0.9, 'and the normal voice straight after it still passes');
  tone(-30, 10, gate);
  assert.ok(tone(-40, 1, gate) > 0.9, 'once the caller is back at their level, a soft stretch 10 dB below it passes again');
});

test('a caller speaking well below the level they spoke at before is still heard', () => {
  const gate = newGate();
  tone(-20, 8, gate);
  assert.ok(tone(-31, 1, gate) > 0.9, '11 dB quieter than the level the gate learned passes at once');
  assert.ok(tone(-31, 20, gate) > 0.9, 'and keeps passing');
  assert.ok(tone(-41, 1, gate) > 0.9, 'and the gate follows the quieter caller down');
});

test('her voice coming back loud through the speakers never raises the bar over the caller', () => {
  const gate = newGate();
  tone(-30, 6, gate);
  tone(SILENT, 30, gate, { her: -20, echo: -10 });
  assert.ok(tone(-30, 1, gate, { her: -20, echo: -40 }) > 0.9, 'the caller talking over her at their normal level passes');
  tone(SILENT, 1, gate, { her: SILENT });
  assert.ok(tone(-30, 1, gate, { her: SILENT }) > 0.9, 'and passes once she has stopped');
});

test('her voice coming back well below the caller is held back while she speaks', () => {
  const gate = newGate();
  tone(-20, 8, gate);
  assert.ok(tone(SILENT, 3, gate, { her: -20, echo: -40 }) < 0.05);
});

function delivered(db, seconds, gate) {
  const out = new Float32Array(BLOCK);
  const blocks = Math.round((seconds * RATE) / BLOCK);
  let power = 0;
  let counted = 0;
  let peak = 0;
  for (let b = 0; b < blocks; b++) {
    gate.apply(sine(db, 200, b * BLOCK), out);
    for (let i = 0; i < BLOCK; i++) peak = Math.max(peak, Math.abs(out[i]));
    if (b >= blocks / 2) {
      for (let i = 0; i < BLOCK; i++) power += out[i] * out[i];
      counted += BLOCK;
    }
  }
  return { level: 10 * Math.log10(power / counted), peak };
}

test('a quiet caller is lifted and a loud one brought down to the same level', () => {
  const quiet = delivered(-42, 30, newGate()).level;
  const loud = delivered(-18, 30, newGate()).level;
  assert.ok(Math.abs(quiet - loud) < 1, `quiet ${quiet.toFixed(1)} dB and loud ${loud.toFixed(1)} dB arrive together`);
  assert.ok(quiet > -26 && quiet < -22, `both arrive near the shared speech level, at ${quiet.toFixed(1)} dB`);
});

test('a caller who shouts after being lifted is held below full scale', () => {
  const gate = newGate();
  delivered(-42, 30, gate);
  assert.ok(delivered(-4, 2, gate).peak <= 0.9, 'the shout is limited instead of clipping');
});

test('silence before the caller first speaks does not pre-lift their first words', () => {
  const gate = newGate();
  tone(SILENT, 20, gate);
  assert.equal(gate.makeupDb, 0, 'nothing is lifted until the caller has been heard');
  const first = delivered(-36, 0.5, gate);
  assert.ok(first.level < -30, `the first half second arrives near the level it was spoken at, ${first.level.toFixed(1)} dB`);
});
