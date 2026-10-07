import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const RATE = 48000;
const BLOCK = 128;

function tone(db, seconds, gate) {
  const amplitude = 10 ** (db / 20) * Math.SQRT2;
  const out = new Float32Array(BLOCK);
  let gain = 0;
  const blocks = Math.round((seconds * RATE) / BLOCK);
  for (let b = 0; b < blocks; b++) {
    const input = Float32Array.from({ length: BLOCK }, (_, i) => amplitude * Math.sin((2 * Math.PI * 200 * (b * BLOCK + i)) / RATE));
    gate.apply(input, out);
    gain = gate.gain;
  }
  return gain;
}

const newGate = () => {
  const run = load('voice-gate.js');
  return new (run('NearVoiceGate'))(RATE);
};

test('a television far below the caller is held back once the caller has spoken', () => {
  const gate = newGate();
  assert.ok(tone(-38, 2, gate) > 0.9, 'before the caller speaks there is nothing to compare with, so it passes');
  assert.ok(tone(-16, 1.5, gate) > 0.9, 'the caller passes');
  assert.ok(tone(-38, 3, gate) < 0.05, 'the television 22 dB below the caller is held back');
  assert.ok(tone(-38, 120, gate) < 0.05, 'and stays held back through two minutes of the agent talking');
});

test('the caller speaking softly still passes, and a short bang does not lock them out', () => {
  const gate = newGate();
  tone(-16, 1.5, gate);
  assert.ok(tone(-26, 1, gate) > 0.9, 'a softer stretch 10 dB below the caller still passes');
  tone(-2, 0.05, gate);
  assert.ok(tone(-20, 1, gate) > 0.9, 'a 50 ms bang does not raise the bar over the caller');
});

test('silence and hiss below the floor are held back', () => {
  const gate = newGate();
  tone(-16, 1, gate);
  assert.ok(tone(-70, 2, gate) < 0.05);
});

test('the television speaking up as soon as the caller stops is held back', () => {
  const gate = newGate();
  tone(-16, 1.5, gate);
  tone(-30, 0.4, gate);
  assert.ok(tone(-30, 0.4, gate) < 0.05, 'a voice 14 dB below the caller is held back within half a second of the caller stopping');
});
