// Synthesised sound effects (WebAudio, no assets). Muted by default; the
// audio context is only created once the user enables sound.
export class Sfx {
  constructor() {
    this.enabled = false;
    this.ctx = null;
    this.loops = new Map();
  }

  setEnabled(on) {
    this.enabled = on;
    if (on && !this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.5;
      this.master.connect(this.ctx.destination);
      this._noise = this._makeNoise();
    }
    if (on) this.ctx.resume();
    if (!on) for (const k of [...this.loops.keys()]) this.stop(k);
  }

  _makeNoise() {
    const len = this.ctx.sampleRate * 2;
    const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  }

  _env(node, t, a, peak, dur) {
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    node.connect(g);
    g.connect(this.master);
    return g;
  }

  _noiseBurst({ dur = 0.3, type = 'bandpass', freq = 800, q = 1, peak = 0.5, sweep = null, loop = false }) {
    const c = this.ctx, t = c.currentTime;
    const src = c.createBufferSource();
    src.buffer = this._noise;
    src.loop = loop;
    const f = c.createBiquadFilter();
    f.type = type; f.frequency.value = freq; f.Q.value = q;
    if (sweep) f.frequency.exponentialRampToValueAtTime(sweep, t + dur);
    src.connect(f);
    const g = this._env(f, t, 0.01, peak, loop ? 9999 : dur);
    src.start(t);
    if (!loop) src.stop(t + dur + 0.05);
    return { src, g };
  }

  _tone({ freq = 440, to = null, dur = 0.2, type = 'sine', peak = 0.3, delay = 0 }) {
    const c = this.ctx, t = c.currentTime + delay;
    const o = c.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    this._env(o, t, 0.005, peak, dur);
    o.start(t);
    o.stop(t + dur + 0.05);
  }

  play(name) {
    if (!this.enabled || !this.ctx) return;
    switch (name) {
      case 'slice': this._noiseBurst({ dur: 0.5, type: 'highpass', freq: 3000, sweep: 6000, peak: 0.15 }); break;
      case 'squelch':
        this._noiseBurst({ dur: 0.25, freq: 400, q: 4, sweep: 150, peak: 0.6 });
        this._tone({ freq: 180, to: 60, dur: 0.2, type: 'triangle', peak: 0.25 });
        break;
      case 'splat': this._noiseBurst({ dur: 0.3, type: 'lowpass', freq: 900, sweep: 200, peak: 0.7 }); break;
      case 'stitch': this._noiseBurst({ dur: 0.12, freq: 2500, q: 6, sweep: 1200, peak: 0.25 }); break;
      case 'pop': this._tone({ freq: 300, to: 900, dur: 0.12, type: 'square', peak: 0.12 }); this._noiseBurst({ dur: 0.2, freq: 500, q: 3, peak: 0.4 }); break;
      case 'snap': this._noiseBurst({ dur: 0.08, type: 'highpass', freq: 1500, peak: 0.6 }); break;
      case 'gurgle':
        for (let i = 0; i < 6; i++) this._tone({ freq: 120 + Math.random() * 200, to: 80, dur: 0.12, type: 'sine', peak: 0.2, delay: i * 0.1 });
        break;
      case 'lever': this._tone({ freq: 90, to: 50, dur: 0.25, type: 'square', peak: 0.15 }); this._noiseBurst({ dur: 0.15, freq: 1200, q: 2, peak: 0.3 }); break;
      case 'zap':
        this._noiseBurst({ dur: 1.3, type: 'highpass', freq: 2000, peak: 0.3 });
        this._tone({ freq: 60, to: 120, dur: 1.2, type: 'sawtooth', peak: 0.2 });
        break;
      case 'laugh':
        for (let i = 0; i < 5; i++) this._tone({ freq: 260 - i * 15, to: 180 - i * 10, dur: 0.12, type: 'sawtooth', peak: 0.1, delay: i * 0.15 });
        break;
      case 'roar': this._tone({ freq: 110, to: 60, dur: 0.7, type: 'sawtooth', peak: 0.2 }); this._noiseBurst({ dur: 0.7, freq: 300, q: 1, peak: 0.3 }); break;
      case 'squeak': this._tone({ freq: 700 + Math.random() * 400, to: 1400, dur: 0.15, type: 'triangle', peak: 0.12 }); break;
      case 'grunt': this._tone({ freq: 140, to: 90, dur: 0.25, type: 'sawtooth', peak: 0.12 }); break;
      case 'whoosh': this._noiseBurst({ dur: 0.4, freq: 600, q: 0.8, sweep: 2400, peak: 0.25 }); break;
      case 'clang':
        for (const f of [420, 637, 911]) this._tone({ freq: f, dur: 0.9, type: 'triangle', peak: 0.12 });
        break;
      case 'fuse': this._noiseBurst({ dur: 1.4, type: 'highpass', freq: 5000, peak: 0.12 }); break;
      case 'slosh': this._noiseBurst({ dur: 0.5, freq: 500, q: 2, sweep: 300, peak: 0.3 }); break;
      case 'boom':
        this._noiseBurst({ dur: 1.6, type: 'lowpass', freq: 1200, sweep: 60, peak: 1 });
        this._tone({ freq: 70, to: 30, dur: 1.2, type: 'sine', peak: 0.8 });
        break;
      case 'sizzle': this._noiseBurst({ dur: 2.0, type: 'highpass', freq: 4000, peak: 0.2 }); break;
      case 'hose': {
        const n = this._noiseBurst({ dur: 0, type: 'bandpass', freq: 1400, q: 0.6, peak: 0.25, loop: true });
        this.loops.set('hose', n);
        break;
      }
      case 'scream':
        this._noiseBurst({ dur: 1.3, freq: 1800, q: 1.5, sweep: 700, peak: 0.6 });
        this._tone({ freq: 620, to: 260, dur: 1.2, type: 'sawtooth', peak: 0.28 });
        this._tone({ freq: 930, to: 380, dur: 1.1, type: 'square', peak: 0.1 });
        break;
      case 'death': this._tone({ freq: 600, to: 120, dur: 0.5, type: 'triangle', peak: 0.15 }); this._noiseBurst({ dur: 0.3, freq: 400, q: 3, peak: 0.4 }); break;
      default: break;
    }
  }

  stop(name) {
    const n = this.loops.get(name);
    if (!n) return;
    this.loops.delete(name);
    try {
      n.g.gain.cancelScheduledValues(this.ctx.currentTime);
      n.g.gain.setTargetAtTime(0, this.ctx.currentTime, 0.1);
      n.src.stop(this.ctx.currentTime + 0.4);
    } catch { /* already stopped */ }
  }
}
