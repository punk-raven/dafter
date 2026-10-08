import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from './harness.mjs';

const best = (...args) => JSON.parse(JSON.stringify(load('client-layout.js')('bestColumns')(...args)));

test('tiles take the column count that makes them largest at the video\'s own shape', () => {
  assert.deepEqual(best(1, 1000, 500, 16 / 9, 8).cols, 1);
  assert.equal(Math.round(best(1, 1000, 500, 16 / 9, 8).width), 889);
  assert.equal(best(2, 1000, 500, 16 / 9, 8).cols, 2);
  assert.equal(best(3, 1000, 600, 16 / 9, 8).cols, 2);
  assert.equal(best(3, 1600, 400, 16 / 9, 8).cols, 3);
  assert.equal(best(4, 1000, 700, 4 / 3, 8).cols, 2);
});
