import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

function element() {
  return { textContent: '', hidden: true, classes: new Set(), classList: { toggle(name, on) { if (on) this.owner.classes.add(name); else this.owner.classes.delete(name); } } };
}

const client = () => load('jstest/browser-stub.js', 'client.js');

test('the microphone is captured with echo cancellation and automatic gain on, whatever the noise filter', () => {
  const run = client();
  const options = run('roomOptionsFrom')({ media: { audio: { echoCancellation: true, noiseCancellation: 'rnnoise' } } });
  assert.deepEqual(JSON.parse(JSON.stringify(options.audioCaptureDefaults)), { autoGainControl: true, echoCancellation: true, noiseSuppression: false });
  const plain = run('roomOptionsFrom')({});
  assert.equal(plain.audioCaptureDefaults.autoGainControl, true);
});

test('the call shows which noise filter is really running, and says so when RNNoise could not load', () => {
  const run = client();
  const label = element();
  label.classList.owner = label;
  run('globalThis').document = { getElementById: () => label };
  run('showNoiseFilter')('rnnoise (wasm)', true);
  assert.equal(label.textContent, 'Noise filter: rnnoise (wasm)');
  assert.equal(label.hidden, false);
  assert.equal(label.classes.has('noise-filter-off'), false);
  run('showNoiseFilter')('browser native, rnnoise (wasm) failed to load', false);
  assert.equal(label.classes.has('noise-filter-off'), true);
  run('showNoiseFilter')('', true);
  assert.equal(label.hidden, true);
});

test('a caption with no words in it makes no transcript line', () => {
  const run = load('client-names.js', 'client-captions.js');
  const made = [];
  run('globalThis').document = { getElementById: () => ({ scrollHeight: 0, scrollTop: 0, clientHeight: 0, querySelector: () => null, appendChild: (l) => made.push(l) }), createElement: () => { const l = { classList: { toggle() {} }, querySelector: () => ({ textContent: '', dataset: {} }) }; return l; }, querySelectorAll: () => [] };
  Object.assign(run('globalThis'), { clearTimeout: () => {}, setTimeout: () => 0 });
  run('captionView').mode = 'live';
  for (const text of ['', '   ', '.', '​']) {
    run('onCaption')({ type: 'transcript.final', payload: { segmentId: `sg_${text.length}`, speaker: { kind: 'human', participantId: 'p_9c2e11aa' }, text } });
  }
  assert.equal(made.length, 0);
  run('onCaption')({ type: 'transcript.final', payload: { segmentId: 'sg_words', speaker: { kind: 'human', participantId: 'p_9c2e11aa' }, text: 'Hmm' } });
  assert.ok(made.length > 0, 'a word, even a backchannel, stays in the live transcript');
});

test('the near-voice gate gets a steady microphone level, so automatic gain cannot lift the room up to the caller', () => {
  const run = client();
  const options = run('roomOptionsFrom')({ media: { audio: { echoCancellation: true, noiseCancellation: 'rnnoise_gated' } } });
  assert.deepEqual(JSON.parse(JSON.stringify(options.audioCaptureDefaults)), { autoGainControl: false, echoCancellation: true, noiseSuppression: false });
});
