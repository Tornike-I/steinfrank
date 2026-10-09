// A monster's own recorded audio from Frankenstein: spoken verdicts, the birth
// scene and its sound set (arrive / working loop / done). Voiced audio runs
// through an analyser so the den can move the monster's mouths with it.
import * as F from '../ai/frankenstein.js';
import { state } from '../core/store.js';

class MonsterVoice {
  constructor() {
    this.ctx = null;
    this.speaking = null;
    this.loopEl = null;
    this.sceneEl = null;
    this.live = null; // () => 0..1 while a live voice conversation is running
  }

  _ensure() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.connect(this.ctx.destination);
      this.buf = new Uint8Array(this.analyser.fftSize);
    }
    this.ctx.resume();
  }

  _play(url, { voiced = false, loop = false, volume = 1, force = false } = {}) {
    if (!url || (!state.settings.sound && !force)) return null;
    this._ensure();
    const el = new Audio();
    el.crossOrigin = 'anonymous';
    el.src = F.artifactUrl(url);
    el.loop = loop;
    el.volume = volume;
    this.ctx.createMediaElementSource(el).connect(voiced ? this.analyser : this.ctx.destination);
    el.play().catch(() => {});
    return el;
  }

  // The birth scene: several voices, so it doesn't drive the mouths. A verdict
  // arriving meanwhile waits for it to end.
  scene(url) {
    this.sceneEl?.pause();
    const el = this._play(url);
    this.sceneEl = el;
    const done = () => { if (this.sceneEl === el) this.sceneEl = null; };
    el?.addEventListener('ended', done);
    el?.addEventListener('error', done);
  }

  // `force` plays even with sound muted (an explicit click on the message).
  speak(url, force = false) {
    const scene = this.sceneEl;
    if (scene && !scene.paused && !scene.ended && !scene.error) {
      const go = () => { scene.removeEventListener('error', go); scene.removeEventListener('ended', go); this.speak(url, force); };
      scene.addEventListener('ended', go);
      scene.addEventListener('error', go);
      return;
    }
    this.speaking?.pause();
    const el = this._play(url, { voiced: true, force });
    if (!el) return;
    this.speaking = el;
    this._duck(true);
    el.addEventListener('ended', () => { if (this.speaking === el) { this.speaking = null; this._duck(false); } });
  }

  sound(url, volume = 0.6) { this._play(url, { volume }); }

  startLoop(url) {
    if (this.loopEl?.dataset.src === url) return;
    this.stopLoop();
    this.loopEl = this._play(url, { loop: true, volume: this.speaking ? 0.06 : 0.25 });
    if (this.loopEl) this.loopEl.dataset.src = url;
  }

  stopLoop() {
    this.loopEl?.pause();
    this.loopEl = null;
  }

  _duck(on) { if (this.loopEl) this.loopEl.volume = on ? 0.06 : 0.25; }

  stopAll() {
    this.sceneEl?.pause();
    this.sceneEl = null;
    this.speaking?.pause();
    this.speaking = null;
    this.stopLoop();
  }

  // Mouth opening, 0..1.
  level() {
    if (this.live) return this.live();
    if (!this.speaking || !this.analyser) return 0;
    this.analyser.getByteTimeDomainData(this.buf);
    let sum = 0;
    for (const v of this.buf) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / this.buf.length) * 5);
  }
}

export const monsterVoice = new MonsterVoice();
