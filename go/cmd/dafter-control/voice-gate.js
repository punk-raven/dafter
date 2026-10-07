const GATE_BELOW_DB = 12;
const GATE_FLOOR_DB = -60;
const GATE_START_DB = -50;
const GATE_RISE_DB_PER_S = 40;
const GATE_FALL_DB_PER_S = 0.02;
const GATE_HOLD_S = 0.25;
const GATE_CLOSED_DB = -35;
const GATE_ATTACK_S = 0.005;
const GATE_RELEASE_S = 0.1;
const GATE_LEVEL_S = 0.03;
const GATE_LOOKAHEAD_S = 0.03;

class NearVoiceGate {
  constructor(rate) {
    this.rate = rate;
    this.power = 0;
    this.voice = GATE_START_DB;
    this.held = 0;
    this.gain = 1;
    this.closed = 10 ** (GATE_CLOSED_DB / 20);
    this.delay = new Float32Array(Math.round(GATE_LOOKAHEAD_S * rate));
    this.at = 0;
  }

  level(block) {
    let sum = 0;
    for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
    const seconds = block.length / this.rate;
    const keep = Math.exp(-seconds / GATE_LEVEL_S);
    this.power = keep * this.power + (1 - keep) * (sum / block.length);
    return 10 * Math.log10(this.power + 1e-12);
  }

  open(block) {
    const seconds = block.length / this.rate;
    const heard = this.level(block);
    if (heard > this.voice) this.voice = Math.min(heard, this.voice + GATE_RISE_DB_PER_S * seconds);
    else this.voice = Math.max(GATE_START_DB, this.voice - GATE_FALL_DB_PER_S * seconds);
    const near = heard > GATE_FLOOR_DB && heard >= this.voice - GATE_BELOW_DB;
    this.held = near ? GATE_HOLD_S : Math.max(0, this.held - seconds);
    return this.held > 0;
  }

  apply(input, output) {
    const target = this.open(input) ? 1 : this.closed;
    const tau = target > this.gain ? GATE_ATTACK_S : GATE_RELEASE_S;
    const step = 1 - Math.exp(-1 / (tau * this.rate));
    for (let i = 0; i < input.length; i++) {
      this.gain += (target - this.gain) * step;
      const delayed = this.delay[this.at];
      this.delay[this.at] = input[i];
      this.at = (this.at + 1) % this.delay.length;
      output[i] = delayed * this.gain;
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
      const output = outputs[0];
      for (let channel = 0; channel < output.length; channel++) {
        if (!input || !input[channel]) {
          output[channel].fill(0);
          continue;
        }
        this.gates[channel] = this.gates[channel] || new NearVoiceGate(sampleRate);
        this.gates[channel].apply(input[channel], output[channel]);
      }
      return true;
    }
  }
  registerProcessor('near-voice-gate', NearVoiceGateProcessor);
}
