// A transparent 2D canvas above the whole UI (pointer-events: none) for brief
// full-screen effects — currently the explosion flash. Kept faint so text
// stays readable.
export class Overlay {
  constructor(canvas) {
    this.cv = canvas;
    this.g = canvas.getContext('2d');
    this.flashA = 0;
    this.flashC = '255,220,160';
    this.dpr = 1;
    this._dirty = true;
  }

  resize(w, h, dpr) {
    this.w = w; this.h = h; this.dpr = Math.min(dpr, 1.5);
    this.cv.width = Math.round(w * this.dpr);
    this.cv.height = Math.round(h * this.dpr);
    this._dirty = true;
  }

  flash(color = '255,220,160', a = 0.35) { this.flashC = color; this.flashA = a; }

  update(dt) {
    if (this.flashA <= 0 && !this._dirty) return;
    const g = this.g;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    if (this.flashA > 0) {
      g.fillStyle = `rgba(${this.flashC},${this.flashA})`;
      g.fillRect(0, 0, this.w, this.h);
      this.flashA = Math.max(0, this.flashA - dt * 1.4);
    }
    // Stay dirty while visible so the last frame gets cleared.
    this._dirty = this.flashA > 0;
  }
}
