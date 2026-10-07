const GATE_BELOW_DB = 12;
const GATE_FLOOR_DB = -60;
const GATE_START_DB = -45;
const GATE_LOWEST_DB = -50;
const GATE_RISE_DB_PER_S = 4;
const GATE_FALL_DB_PER_S = 1;
const GATE_IDLE_DB_PER_S = 0.02;
const GATE_HOLD_S = 0.25;
const GATE_CLOSED_DB = -35;
const GATE_ATTACK_S = 0.005;
const GATE_RELEASE_S = 0.1;
const GATE_LEVEL_S = 0.03;
const GATE_LOOKAHEAD_S = 0.03;
const FAR_END_FLOOR_DB = -50;
const FAR_END_HOLD_S = 0.5;
const MAKEUP_VOICE_DB = -22;
const MAKEUP_MAX_DB = 24;
const MAKEUP_MIN_DB = -12;
const MAKEUP_RISE_DB_PER_S = 6;
const MAKEUP_FALL_DB_PER_S = 12;
const LIMIT_CEILING = 0.89;
const LIMIT_ATTACK_S = 0.002;
const LIMIT_RELEASE_S = 0.2;

class SmoothedLevel {
  constructor(rate) {
    this.rate = rate;
    this.power = 0;
  }

  of(block) {
    let sum = 0;
    for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
    const keep = Math.exp(-block.length / this.rate / GATE_LEVEL_S);
    this.power = keep * this.power + (1 - keep) * (sum / block.length);
    return 10 * Math.log10(this.power + 1e-12);
  }
}

class NearVoiceGate {
  constructor(rate) {
    this.rate = rate;
    this.heard = new SmoothedLevel(rate);
    this.farEnd = new SmoothedLevel(rate);
    this.voice = GATE_START_DB;
    this.makeupDb = 0;
    this.held = 0;
    this.playing = 0;
    this.gain = 1;
    this.limit = 1;
    this.closed = 10 ** (GATE_CLOSED_DB / 20);
    this.delay = new Float32Array(Math.round(GATE_LOOKAHEAD_S * rate));
    this.at = 0;
    this.peaks = new Float32Array(Math.ceil(this.delay.length / 128) + 2);
    this.peakAt = 0;
  }

  makeup() {
    return 10 ** (this.makeupDb / 20);
  }

  follow(seconds) {
    const wanted = Math.min(MAKEUP_MAX_DB, Math.max(MAKEUP_MIN_DB, MAKEUP_VOICE_DB - this.voice));
    const step = wanted - this.makeupDb;
    this.makeupDb += Math.max(-MAKEUP_FALL_DB_PER_S * seconds, Math.min(MAKEUP_RISE_DB_PER_S * seconds, step));
  }

  open(block, far) {
    const seconds = block.length / this.rate;
    const heard = this.heard.of(block);
    const near = heard > GATE_FLOOR_DB && heard >= this.voice - GATE_BELOW_DB;
    if (!this.farEndPlaying(far, seconds)) this.learn(heard, near, seconds);
    this.held = near ? GATE_HOLD_S : Math.max(0, this.held - seconds);
    return this.held > 0;
  }

  farEndPlaying(far, seconds) {
    const playing = far !== undefined && this.farEnd.of(far) > FAR_END_FLOOR_DB;
    this.playing = playing ? FAR_END_HOLD_S : Math.max(0, this.playing - seconds);
    return this.playing > 0;
  }

  learn(heard, near, seconds) {
    if (!near) this.voice -= GATE_IDLE_DB_PER_S * seconds;
    else if (heard > this.voice) this.voice = Math.min(heard, this.voice + GATE_RISE_DB_PER_S * seconds);
    else this.voice = Math.max(heard, this.voice - GATE_FALL_DB_PER_S * seconds);
    this.voice = Math.max(GATE_LOWEST_DB, this.voice);
    if (near) this.follow(seconds);
  }

  ahead(input) {
    let peak = 0;
    for (let i = 0; i < input.length; i++) peak = Math.max(peak, Math.abs(input[i]));
    this.peaks[this.peakAt] = peak;
    this.peakAt = (this.peakAt + 1) % this.peaks.length;
    return Math.max(...this.peaks);
  }

  apply(input, output, far) {
    const target = this.open(input, far) ? 1 : this.closed;
    const makeup = this.makeup();
    const loudest = this.ahead(input) * makeup;
    const limit = loudest > LIMIT_CEILING ? LIMIT_CEILING / loudest : 1;
    const step = 1 - Math.exp(-1 / ((target > this.gain ? GATE_ATTACK_S : GATE_RELEASE_S) * this.rate));
    const limitStep = 1 - Math.exp(-1 / ((limit < this.limit ? LIMIT_ATTACK_S : LIMIT_RELEASE_S) * this.rate));
    for (let i = 0; i < input.length; i++) {
      this.gain += (target - this.gain) * step;
      this.limit += (limit - this.limit) * limitStep;
      const delayed = this.delay[this.at];
      this.delay[this.at] = input[i];
      this.at = (this.at + 1) % this.delay.length;
      output[i] = delayed * this.gain * makeup * this.limit;
    }
  }
}

if (typeof AudioWorkletProcessor === 'function') {
  class NearVoiceGateProcessor extends AudioWorkletProcessor {
    constructor() {
      super();
      this.gates = [];
    }

    process(inputs, outputs) {
      const input = inputs[0];
      const far = inputs[1] && inputs[1][0];
      const output = outputs[0];
      for (let channel = 0; channel < output.length; channel++) {
        if (!input || !input[channel]) {
          output[channel].fill(0);
          continue;
        }
        this.gates[channel] = this.gates[channel] || new NearVoiceGate(sampleRate);
        this.gates[channel].apply(input[channel], output[channel], far);
      }
      return true;
    }
  }
  registerProcessor('near-voice-gate', NearVoiceGateProcessor);
}
