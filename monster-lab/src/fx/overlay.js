// A transparent 2D canvas above the whole UI (pointer-events: none) for
// "lens" effects: goo splats on the stage glass, flashes, and the hose wash
// that sweeps the screen top to bottom. Kept faint so text stays readable.
export class Overlay {
  constructor(canvas) {
    this.cv = canvas;
    this.g = canvas.getContext('2d');
    this.splats = [];
    this.drops = [];
    this.flashA = 0;
    this.flashC = '255,220,160';
    this.washY = null;
    this.dpr = 1;
    this._dirty = true;
  }

  resize(w, h, dpr) {
    this.w = w; this.h = h; this.dpr = Math.min(dpr, 1.5);
    this.cv.width = Math.round(w * this.dpr);
    this.cv.height = Math.round(h * this.dpr);
    this._dirty = true;
  }

  // Goo hitting the stage "glass"; confined to a rect (the stage).
  splat(rect, color = '120,255,60', n = 6) {
    for (let i = 0; i < n; i++) {
      this.splats.push({
        x: rect.x + Math.random() * rect.w, y: rect.y + Math.random() * rect.h * 0.8,
        r: 10 + Math.random() * 34, color, a: 0.55, vy: 6 + Math.random() * 14, life: 7 + Math.random() * 3,
        bottom: rect.y + rect.h, seed: Math.random() * 100,
      });
    }
  }

  flash(color = '255,220,160', a = 0.35) { this.flashC = color; this.flashA = a; }

  // Sweeps a sheet of water down the screen. onFront(y) reports progress.
  wash(duration = 1.8, onFront) {
    return new Promise((resolve) => {
      this.washY = 0;
      this._wash = { t: 0, duration, onFront, resolve };
    });
  }

  get active() {
    return this.splats.length || this.drops.length || this.flashA > 0 || this.washY !== null || this._dirty;
  }

  update(dt) {
    if (!this.active) return;
    this._dirty = false;
    const g = this.g;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.w, this.h);

    if (this._wash) {
      const w = this._wash;
      w.t += dt;
      const u = Math.min(1, w.t / w.duration);
      this.washY = (u * u * 0.4 + u * 0.6) * (this.h + 140);
      w.onFront?.(this.washY);
      // Leave drips behind the front.
      for (let i = 0; i < 6; i++) {
        this.drops.push({ x: Math.random() * this.w, y: this.washY - Math.random() * 60, len: 10 + Math.random() * 40, vy: 140 + Math.random() * 200, life: 0.9 + Math.random() * 0.8 });
      }
      this.splats = this.splats.filter((s) => s.y > this.washY);
      if (u >= 1) { this._wash = null; this.washY = null; w.resolve(); }
    }

    // Splats ooze downward and fade.
    this.splats = this.splats.filter((s) => {
      s.life -= dt;
      s.y += s.vy * dt * 0.3;
      s.a = Math.min(0.55, s.life * 0.2);
      if (s.y > s.bottom) s.life -= dt * 4;
      g.fillStyle = `rgba(${s.color},${s.a})`;
      g.beginPath();
      for (let k = 0; k <= 12; k++) {
        const ang = (k / 12) * Math.PI * 2;
        const rr = s.r * (1 + Math.sin(ang * 5 + s.seed) * 0.18);
        g.lineTo(s.x + Math.cos(ang) * rr, s.y + Math.sin(ang) * rr * 0.9);
      }
      g.fill();
      // A tapering drip that oozes down from the blob, ending in a bead.
      const dl = s.r * (0.8 + (7 - Math.min(7, s.life)) * 0.25);
      const dx = s.x + Math.sin(s.seed) * s.r * 0.3;
      g.beginPath();
      g.moveTo(dx - s.r * 0.22, s.y + s.r * 0.5);
      g.quadraticCurveTo(dx - s.r * 0.08, s.y + dl * 0.7, dx - s.r * 0.1, s.y + dl);
      g.arc(dx, s.y + dl, s.r * 0.13, Math.PI, 0, true);
      g.quadraticCurveTo(dx + s.r * 0.08, s.y + dl * 0.7, dx + s.r * 0.22, s.y + s.r * 0.5);
      g.fill();
      g.fillStyle = `rgba(255,255,255,${s.a * 0.5})`;
      g.beginPath();
      g.arc(s.x - s.r * 0.3, s.y - s.r * 0.3, s.r * 0.18, 0, Math.PI * 2);
      g.fill();
      return s.life > 0;
    });

    if (this.washY !== null) {
      const y = this.washY;
      const grad = g.createLinearGradient(0, y - 220, 0, y + 20);
      grad.addColorStop(0, 'rgba(170,215,255,0)');
      grad.addColorStop(0.75, 'rgba(170,215,255,0.16)');
      grad.addColorStop(1, 'rgba(230,245,255,0.32)');
      g.fillStyle = grad;
      g.beginPath();
      g.moveTo(0, y - 220);
      g.lineTo(this.w, y - 220);
      for (let x = this.w; x >= 0; x -= 24) g.lineTo(x, y + Math.sin(x * 0.03 + y * 0.05) * 10);
      g.closePath();
      g.fill();
      g.strokeStyle = 'rgba(255,255,255,0.45)';
      g.lineWidth = 2;
      g.beginPath();
      for (let x = 0; x <= this.w; x += 24) g.lineTo(x, y + Math.sin(x * 0.03 + y * 0.05) * 10);
      g.stroke();
    }

    this.drops = this.drops.filter((d) => {
      d.life -= dt;
      d.y += d.vy * dt;
      g.strokeStyle = `rgba(210,235,255,${Math.min(0.35, d.life * 0.4)})`;
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(d.x, d.y - d.len);
      g.lineTo(d.x, d.y);
      g.stroke();
      return d.life > 0 && d.y < this.h + 40;
    });

    if (this.flashA > 0) {
      g.fillStyle = `rgba(${this.flashC},${this.flashA})`;
      g.fillRect(0, 0, this.w, this.h);
      this.flashA = Math.max(0, this.flashA - dt * 1.4);
    }
    // Stay dirty while anything is visible so the final frame gets cleared.
    this._dirty = !!(this.splats.length || this.drops.length || this.flashA > 0 || this.washY !== null);
  }
}
