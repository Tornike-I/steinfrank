// The creature layer: a full-viewport orthographic scene rendered on the
// canvas *behind* the DOM. Monsters wander the margins and simply vanish
// behind the opaque chat column; they can never cover text or catch clicks.
// World units here are CSS pixels: x right, y up (screen y is negated).
import * as THREE from 'three';
import { setPallor, setDeadEyes, disposeMonster } from '../monsters/monsterGen.js';
import { buildFor } from '../monsters/registry.js';
import { animateMonster, hopHeight } from '../monsters/monsterAnim.js';

const FPS = 12;
const TILT = 0.32;
const ACID = new THREE.Color(0x7aff3a);
const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

const circleGeo = (() => {
  const g = new THREE.CircleGeometry(1, 14);
  const p = g.attributes.position;
  for (let i = 1; i < p.count; i++) {
    const a = Math.atan2(p.getY(i), p.getX(i));
    const r = 1 + Math.sin(a * 5) * 0.12 + Math.sin(a * 3 + 1) * 0.1;
    p.setXY(i, p.getX(i) * r, p.getY(i) * r);
  }
  return g;
})();

export class CreatureLayer {
  constructor({ getZones }) {
    this.getZones = getZones;
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(0, 1, 0, -1, -20000, 20000);
    this.camera.position.z = 10000;
    this.scene.add(new THREE.HemisphereLight(0xd8e0ff, 0x302010, 1.4));
    const key = new THREE.DirectionalLight(0xffe0b0, 2.6);
    key.position.set(-0.6, 0.8, 1);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x8ab0ff, 1.2);
    rim.position.set(1, 0.4, -0.6);
    this.scene.add(rim);

    this.creatures = new Map();
    this.decals = [];
    this.parts = [];
    this.sprites = [];
    this.t = 0;
    this._acc = 0;
    this.shake = 0;
    this.w = 1; this.h = 1;

    this.partMeshWet = makePartPool(this.scene, false, 700);
    this.partMeshGlow = makePartPool(this.scene, true, 700);

    this.flash = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ color: 0xffe2a0, transparent: true, opacity: 0, depthTest: false }));
    this.flash.renderOrder = 10;
    this.scene.add(this.flash);
  }

  get unit() { return THREE.MathUtils.clamp(this.h * 0.12, 60, 130); }
  // Pixels per monster unit; small monsters get a boost so none are specks.
  _unitFor(m) { return this.unit * THREE.MathUtils.clamp(0.95 / m.height, 0.9, 1.5); }

  resize(w, h) {
    this.w = w; this.h = h;
    this.camera.left = 0; this.camera.right = w;
    this.camera.top = 0; this.camera.bottom = -h;
    this.camera.updateProjectionMatrix();
    this.flash.scale.set(w, h, 1);
    this.flash.position.set(w / 2, -h / 2, 9000);
  }

  count() {
    let n = 0;
    for (const c of this.creatures.values()) if (c.state !== 'dissolving' && !c.temporary) n++;
    return n;
  }

  // An existing assistant answers a lab question: its counterpart turns to
  // face the viewer, charges toward the screen growing huge, and screams.
  // If it was destroyed, a temporary stand-in walks in for the transition.
  summon(rec, { onScream } = {}) {
    return new Promise((resolve) => {
      let c = [...this.creatures.values()].find((x) => x.defId === rec.defId && x.state === 'alive');
      let temporary = false;
      if (!c) {
        c = this._make({ ...rec, id: `tmp-${rec.defId}-${Date.now()}` });
        const fromLeft = Math.random() < 0.5;
        c.x = fromLeft ? -80 : this.w + 80;
        c.y = this.h * 0.86;
        c.temporary = temporary = true;
        this._place(c);
      }
      c.state = 'summoned';
      c.sum = { t: 0, from: V(c.x, c.y), home: V(c.x, c.y), s0: c.scale, onScream, resolve, temporary, screamed: false };
    });
  }

  // ------------------------------------------------------------- creation
  // rec: { id, seed, theme?, defId?, name? }
  _make(rec) {
    const { id, seed } = rec;
    const m = buildFor(seed, rec.theme);
    const group = new THREE.Group();
    const tilt = new THREE.Group();
    tilt.rotation.x = TILT;
    tilt.add(m.root);
    group.add(tilt);
    const shadow = new THREE.Mesh(circleGeo, new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35, depthWrite: false }));
    shadow.scale.set(m.width * 0.55, m.width * 0.16, 1);
    shadow.position.z = -40;
    group.add(shadow);
    this.scene.add(group);
    const label = rec.name ? makeLabel(rec.name) : null;
    if (label) this.scene.add(label);
    const c = {
      id, seed, m, group, tilt, shadow, label, defId: rec.defId || null, theme: rec.theme || null, name: rec.name || null,
      x: 0, y: 0, z: 0, scale: this._unitFor(m), yaw: 0, yawT: 0,
      state: 'alive', mode: 'idle', timer: 1 + Math.random() * 2, target: null,
      phase: Math.random() * 6, walk: 0, excite: 0, t: 0,
      flies: [],
    };
    this.creatures.set(id, c);
    return c;
  }

  restore(list) {
    for (const s of list) {
      const c = this._make(s);
      const p = this._clampToZones(s.x * this.w, s.y * this.h);
      c.x = p.x; c.y = p.y;
      if (s.state === 'dead') this._makeCorpse(c, s.side ?? 1, true);
      this._place(c);
    }
  }

  // Hand-off from the lab: starts at a screen point with a lab-sized scale
  // and leaps down into the margins.
  spawnFromStage({ sx, sy, spx, ...rec }) {
    const c = this._make(rec);
    c.x = sx; c.y = sy;
    c.state = 'arriving';
    c.arrive = { t: 0, from: V(sx, sy), to: this._landingSpot(sx, sy), s0: spx, dur: 1.0 };
    c.scale = spx;
    this._place(c);
    return c;
  }

  _landingSpot(sx, sy) {
    const z = this.getZones();
    const margins = z.margins.filter((r) => r.x1 - r.x0 > 50);
    const pool = margins.length ? margins : [z.all];
    let best = null, bd = Infinity;
    for (const r of pool) {
      const x = THREE.MathUtils.clamp(sx, r.x0 + 20, r.x1 - 20);
      const y = THREE.MathUtils.clamp(sy + 120, r.y0 + 40, r.y1 - 20);
      const d = Math.hypot(x - sx, y - sy) + Math.random() * 80;
      if (d < bd) { bd = d; best = V(x, y); }
    }
    return best;
  }

  _clampToZones(x, y) {
    const a = this.getZones().all;
    return { x: THREE.MathUtils.clamp(x, a.x0 + 10, a.x1 - 10), y: THREE.MathUtils.clamp(y, a.y0 + 20, a.y1 - 10) };
  }

  _randomTarget() {
    const z = this.getZones();
    const margins = z.margins.filter((r) => r.x1 - r.x0 > 40 && r.y1 - r.y0 > 60);
    // Mostly the margins; sometimes a stroll behind the chat column.
    const r = margins.length && Math.random() < 0.8 ? margins[Math.floor(Math.random() * margins.length)] : z.all;
    return V(r.x0 + 20 + Math.random() * Math.max(1, r.x1 - r.x0 - 40), r.y0 + 30 + Math.random() * Math.max(1, r.y1 - r.y0 - 50));
  }

  // --------------------------------------------------------------- deaths
  kill(id, cause = 'regen') {
    const c = this.creatures.get(id);
    if (!c || c.state !== 'alive') return false;
    c.state = 'dying';
    c.die = { t: 0, side: Math.random() < 0.5 ? -1 : 1 };
    return true;
  }

  _makeCorpse(c, side, instant = false) {
    c.state = 'dead';
    c.side = side;
    for (const f of c.flies) this.scene.remove(f.mesh);
    c.flies = [];
    setPallor(c.m, 0.55);
    setDeadEyes(c.m, true);
    c.tilt.rotation.z = side * Math.PI / 2 * 0.94;
    c.tilt.rotation.y = 0;
    c.m.body.rotation.set(0, 0, 0);
    c.pool = this._decal(c.x, c.y + 4, c.m.width * c.scale * 0.6, 0x5a0507, 0.92, instant ? 1 : 0.05);
    c.pool.grow = instant ? 0 : 1;
    c.pool.owner = c;
    // Flies.
    for (let i = 0; i < 2; i++) {
      const f = new THREE.Mesh(new THREE.SphereGeometry(1.6, 6, 4), new THREE.MeshBasicMaterial({ color: 0x0a0a0a }));
      this.scene.add(f);
      c.flies.push({ mesh: f, a: Math.random() * 6, r: 14 + Math.random() * 10 });
    }
  }

  _remove(c) {
    this.scene.remove(c.group);
    if (c.label) { this.scene.remove(c.label); c.label.material.map.dispose(); c.label.material.dispose(); }
    for (const f of c.flies) this.scene.remove(f.mesh);
    if (c.pool) c.pool.owner = null;
    disposeMonster(c.m);
    this.creatures.delete(c.id);
  }

  clear() {
    for (const c of [...this.creatures.values()]) this._remove(c);
    for (const d of this.decals) this.scene.remove(d.mesh);
    for (const s of this.sprites) this.scene.remove(s.mesh);
    this.decals = []; this.sprites = []; this.parts = [];
  }

  serialize() {
    const out = [];
    for (const c of this.creatures.values()) {
      if (c.state === 'dissolving' || c.temporary) continue;
      const dead = c.state === 'dead' || c.state === 'dying' || c.state === 'flung';
      const pos = c.state === 'arriving' ? c.arrive.to : V(c.x, c.y);
      out.push({ id: c.id, seed: c.seed, defId: c.defId, theme: c.theme, name: c.name, state: dead ? 'dead' : 'alive', x: pos.x / this.w, y: pos.y / this.h, side: c.side ?? 1 });
    }
    return out;
  }

  // ------------------------------------------------------------- cleanup
  // Chain-reaction explosion across the creature layer.
  explodeAll() {
    const list = [...this.creatures.values()].filter((c) => c.state !== 'dissolving');
    this.flashT = 1;
    this.shake = 1;
    const centers = list.length ? list : [{ x: this.w * 0.2, y: this.h * 0.6 }, { x: this.w * 0.8, y: this.h * 0.6 }];
    centers.forEach((c, i) => {
      setTimeout(() => {
        this._blast(c.x, c.y - 20);
        if (c.m) this._fling(c);
      }, i * 90);
    });
    this._comic(centers[0].x, centers[0].y - 80, 'KA-BLAM!');
    if (centers.length > 3) this._comic(centers[centers.length - 1].x, centers[centers.length - 1].y - 70, 'SPLORCH!');
    return wait(900 + centers.length * 90 + 700);
  }

  _blast(x, y) {
    for (let i = 0; i < 46; i++) {
      const a = Math.random() * Math.PI * 2, s = 60 + Math.random() * 280;
      this.parts.push({ glow: true, x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 60, life: 0.5 + Math.random() * 0.4, max: 0.9, size: 8 + Math.random() * 16, color: [0xfff1a0, 0xffb030, 0xff6a1a, 0xff3a10][i % 4], g: -80, grow: 1.5 });
    }
    for (let i = 0; i < 22; i++) {
      const a = Math.random() * Math.PI * 2, s = 30 + Math.random() * 90;
      this.parts.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 40, life: 1.4, max: 1.4, size: 12 + Math.random() * 18, color: [0x2a2620, 0x3a3630, 0x4a4438][i % 3], g: -60, grow: 2.2 });
    }
    this._decal(x, y + 20, 50 + Math.random() * 30, 0x140f0a, 0.7);
    this._gore(x, y, 22);
  }

  _gore(x, y, n) {
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + (Math.random() - 0.5) * 2.6, s = 80 + Math.random() * 260;
      this.parts.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 1.2, max: 1.2, size: 2.5 + Math.random() * 4, color: Math.random() < 0.3 ? 0xa0101a : 0x6a0408, g: 700, splat: true });
    }
  }

  _fling(c) {
    if (c.state === 'arriving') { c.x = c.arrive.to.x; c.y = c.arrive.to.y; c.scale = this._unitFor(c.m); }
    c.state = 'flung';
    c.fling = { vx: (Math.random() - 0.5) * 380, vy: -420 - Math.random() * 260, floor: c.y + (Math.random() - 0.3) * 60, spin: (Math.random() - 0.5) * 14, h: 0 };
    if (c.pool) { c.pool.fade = true; c.pool = null; }
    setPallor(c.m, 0.4, new THREE.Color(0x2a2018));
    setDeadEyes(c.m, true);
  }

  dissolveAll() {
    // Acid rain from the top of the screen, then everything melts.
    for (let i = 0; i < 140; i++) {
      this.parts.push({ glow: true, x: Math.random() * this.w, y: -Math.random() * this.h * 0.4, vx: 0, vy: 300 + Math.random() * 300, life: 1.6, max: 1.6, size: 3 + Math.random() * 4, color: 0x9aff4a, g: 400 });
    }
    const list = [...this.creatures.values()];
    list.forEach((c, i) => setTimeout(() => {
      if (!this.creatures.has(c.id)) return;
      if (c.state === 'flung') { c.y = c.fling.floor; this._makeCorpse(c, c.side || 1, true); }
      c.state = 'dissolving';
      c.dis = { t: 0 };
      this._decal(c.x, c.y + 4, c.m.width * c.scale * 0.8, 0x4aaa1a, 0.85, 0.1).grow = 1;
    }, 500 + i * 110));
    return wait(500 + list.length * 110 + 1900);
  }

  // Water front at screen y: everything above it is washed away.
  washTo(y) {
    for (const d of this.decals) {
      if (!d.washed && d.y < y + 20) { d.washed = true; d.fade = true; }
    }
    for (const s of this.sprites) if (s.y < y) s.fade = true;
    for (const c of [...this.creatures.values()]) {
      if (c.y < y && c.state !== 'arriving' && c.state !== 'alive') this._remove(c);
    }
  }

  // --------------------------------------------------------------- decals
  _decal(x, y, r, color, opacity = 0.9, startScale = 1) {
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(circleGeo, mat);
    // Flip instead of rotating: rotation would skew the squashed ellipse.
    const d = { mesh, x, y, r, s: startScale, grow: startScale < 1 ? 1 : 0, fade: false, washed: false, fx: Math.random() < 0.5 ? -1 : 1 };
    mesh.position.set(x, -y, y - 60);
    mesh.scale.set(r * d.s * d.fx, r * 0.42 * d.s, 1);
    this.scene.add(mesh);
    this.decals.push(d);
    if (this.decals.length > 120) { const old = this.decals.shift(); this.scene.remove(old.mesh); }
    return d;
  }

  _comic(x, y, text) {
    const cv = document.createElement('canvas');
    cv.width = 512; cv.height = 320;
    const g = cv.getContext('2d');
    g.translate(256, 160);
    g.beginPath();
    for (let i = 0; i < 28; i++) {
      const a = (i / 28) * Math.PI * 2, r = i % 2 ? 110 : 160 + Math.random() * 40;
      g.lineTo(Math.cos(a) * r * 1.4, Math.sin(a) * r * 0.85);
    }
    g.closePath();
    g.fillStyle = '#ffd23a'; g.fill();
    g.lineWidth = 10; g.strokeStyle = '#2a0a04'; g.stroke();
    g.rotate(-0.12);
    g.font = '900 78px "Lilita One", Impact, sans-serif';
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.lineWidth = 12; g.strokeStyle = '#2a0a04'; g.strokeText(text, 0, 6);
    g.fillStyle = '#d8241a'; g.fillText(text, 0, 6);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthTest: false }));
    mesh.renderOrder = 5;
    const x2 = THREE.MathUtils.clamp(x, 140, this.w - 140);
    const y2 = THREE.MathUtils.clamp(y, 120, this.h - 120);
    mesh.position.set(x2, -y2, 8000);
    this.scene.add(mesh);
    this.sprites.push({ mesh, t: 0, y: y2, life: 1.6 });
  }

  // --------------------------------------------------------------- update
  _place(c) {
    const hop = hopHeight(c.m, c.phase, c.walk) * c.scale;
    let lift = 0;
    if (c.state === 'dead' || c.state === 'dissolving') lift = c.m.width * 0.42 * c.scale;
    c.group.position.set(c.x, -c.y + hop + lift, c.y);
    c.tilt.scale.setScalar(c.scale);
    c.shadow.position.set(0, -lift - hop, -40);
    c.shadow.scale.set(c.m.width * 0.55 * c.scale * (c.state === 'dead' ? 1.6 : 1), c.m.width * 0.16 * c.scale, 1);
    c.shadow.visible = c.state !== 'flung' && c.state !== 'arriving';
    if (c.label) {
      c.label.visible = c.state === 'alive' || c.state === 'returning';
      c.label.position.set(c.x, -c.y + c.m.height * c.scale + hop + 16, c.y + 300);
    }
  }

  // The living monster under a screen point (for clicking), or null.
  pick(x, y) {
    let best = null;
    for (const c of this.creatures.values()) {
      if (c.state !== 'alive' || !c.defId) continue;
      const w = Math.max(30, c.m.width * c.scale * 0.6), h = c.m.height * c.scale;
      const onLabel = c.label && Math.abs(x - c.x) < c.label.scale.x / 2 && Math.abs(y - (c.y - h - 16)) < c.label.scale.y / 2;
      if (onLabel || (Math.abs(x - c.x) < w && y < c.y + 6 && y > c.y - h - 4)) {
        if (!best || c.y > best.y) best = c;
      }
    }
    return best;
  }

  update(dt) {
    this.t += dt;
    // Particles, sprites, decals and flash animate smoothly.
    this._updateParticles(dt);
    for (const d of this.decals) {
      if (d.grow) { d.s = Math.min(1, d.s + dt * 0.6); if (d.s >= 1) d.grow = 0; }
      if (d.fade) { d.mesh.material.opacity -= dt * 2.5; }
      d.mesh.scale.set(d.r * d.s * d.fx, d.r * 0.42 * d.s, 1);
    }
    this.decals = this.decals.filter((d) => {
      if (d.mesh.material.opacity <= 0) { this.scene.remove(d.mesh); d.mesh.material.dispose(); return false; }
      return true;
    });
    this.sprites = this.sprites.filter((s) => {
      s.t += dt;
      const pop = Math.min(1, s.t / 0.15);
      const sc = (0.6 + pop * 0.5) * (1 + Math.sin(s.t * 30) * 0.02);
      s.mesh.scale.set(330 * sc, 206 * sc, 1);
      if (s.t > s.life || s.fade) s.mesh.material.opacity -= dt * 4;
      if (s.mesh.material.opacity <= 0) { this.scene.remove(s.mesh); return false; }
      return true;
    });
    if (this.flashT > 0) { this.flashT = Math.max(0, this.flashT - dt * 2.2); this.flash.material.opacity = this.flashT * 0.85; }
    this.shake = Math.max(0, this.shake - dt * 1.5);
    const sh = this.shake * 14;
    this.camera.position.x = (Math.random() - 0.5) * sh;
    this.camera.position.y = (Math.random() - 0.5) * sh;

    // Smooth physics for flung / arriving bodies.
    for (const c of this.creatures.values()) {
      if (c.state === 'flung') {
        const f = c.fling;
        f.vy += 1400 * dt;
        c.x += f.vx * dt; c.y += f.vy * dt;
        c.tilt.rotation.z += f.spin * dt;
        c.x = THREE.MathUtils.clamp(c.x, 10, this.w - 10);
        if (c.y >= f.floor && f.vy > 0) {
          c.y = f.floor;
          this._gore(c.x, c.y, 8);
          this._makeCorpse(c, Math.random() < 0.5 ? -1 : 1);
        }
        this._place(c);
      }
    }

    // Stop-motion tick for everything with a puppet pose.
    this._acc += dt;
    if (this._acc < 1 / FPS) return;
    const step = this._acc;
    this._acc = 0;
    for (const c of [...this.creatures.values()]) this._tick(c, step);
  }

  _tick(c, dt) {
    c.t += dt;
    const m = c.m;
    if (c.state === 'arriving') {
      const a = c.arrive;
      a.t += dt;
      const u = Math.min(1, a.t / a.dur);
      const e = u * u * (3 - 2 * u);
      c.x = a.from.x + (a.to.x - a.from.x) * e;
      c.y = a.from.y + (a.to.y - a.from.y) * u - Math.sin(u * Math.PI) * 160;
      c.scale = a.s0 + (this._unitFor(m) - a.s0) * Math.min(1, u * 1.4);
      c.tilt.rotation.y = Math.sin(u * Math.PI) * 0.8;
      animateMonster(m, c.t, { walk: 0, excite: 1 - u });
      if (u >= 1) {
        c.state = 'alive'; c.mode = 'idle'; c.timer = 0.6; c.scale = this._unitFor(m);
        this._decal(c.x, c.y + 4, m.width * c.scale * 0.35, 0x5a0507, 0.5).fade = false;
      }
      this._place(c);
      return;
    }
    if (c.state === 'alive') {
      if (c.mode === 'walk') {
        const dx = c.target.x - c.x, dy = c.target.y - c.y;
        const dist = Math.hypot(dx, dy);
        const speed = 34 * m.speed * (c.scale / 60);
        const moving = m.gait !== 'hop' || Math.sin(c.phase) > 0;
        if (dist < 4) { c.mode = 'idle'; c.timer = 1 + Math.random() * 3.5; }
        else if (moving) {
          const k = Math.min(1, (speed * dt * (m.gait === 'hop' ? 2 : 1)) / dist);
          c.x += dx * k; c.y += dy * k;
        }
        c.yawT = Math.atan2(dx, dy * 1.6);
        c.walk = Math.min(1, c.walk + dt * 4);
        c.phase += dt * (m.gait === 'scuttle' ? 13 : 8) * m.speed;
      } else {
        c.walk = Math.max(0, c.walk - dt * 4);
        c.timer -= dt;
        if (c.timer < 0) {
          c.target = this._randomTarget();
          c.mode = 'walk';
          c.excite = Math.random() < 0.2 ? 1 : 0;
        }
        if (Math.random() < 0.02) c.yawT = (Math.random() - 0.5) * 1.6;
      }
      c.excite = Math.max(0, c.excite - dt * 0.8);
      let d = c.yawT - c.yaw;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      c.yaw += d * 0.35;
      c.tilt.rotation.y = c.yaw;
      animateMonster(m, c.t, { walk: c.walk, phase: c.phase, excite: c.excite });
      this._place(c);
      return;
    }
    if (c.state === 'summoned' || c.state === 'returning') {
      this._tickSummon(c, dt);
      return;
    }
    if (c.state === 'dying') {
      const k = c.die;
      k.t += dt;
      if (k.t < 0.45) {
        c.group.position.x = c.x + (Math.random() - 0.5) * 6;
        animateMonster(m, c.t, { excite: 1 });
      } else {
        if (!k.burst) { k.burst = true; this._gore(c.x, c.y - m.height * c.scale * 0.5, 26); }
        const u = Math.min(1, (k.t - 0.45) / 0.5);
        c.tilt.rotation.z = k.side * Math.PI / 2 * 0.94 * u * u;
        if (u >= 1) this._makeCorpse(c, k.side);
        this._place(c);
      }
      return;
    }
    if (c.state === 'dead') {
      for (const f of c.flies) {
        f.a += dt * (3 + Math.random() * 2);
        f.mesh.position.set(c.x + Math.cos(f.a) * f.r + (Math.random() - 0.5) * 4, -c.y + 26 + Math.sin(f.a * 1.7) * 10, c.y + 200);
      }
      
      return;
    }
    if (c.state === 'dissolving') {
      const k = c.dis;
      k.t += dt;
      const u = Math.min(1, k.t / 1.7);
      setPallor(m, Math.min(1, u * 1.5), ACID);
      c.tilt.scale.set(c.scale * (1 + u * 0.35), c.scale * Math.max(0.05, 1 - u), c.scale);
      for (const f of c.flies) f.mesh.visible = false;
      for (let i = 0; i < 3; i++) {
        this.parts.push({ glow: true, x: c.x + (Math.random() - 0.5) * m.width * c.scale, y: c.y - Math.random() * 20, vx: 0, vy: -40 - Math.random() * 40, life: 0.8, max: 0.8, size: 2 + Math.random() * 4, color: 0xaaff5a, g: -20 });
      }
      if (u >= 1) this._remove(c);
    }
  }

  _tickSummon(c, dt) {
    const k = c.sum, m = c.m;
    k.t += dt;
    const unit = this._unitFor(m);
    if (c.state === 'returning') {
      const u = Math.min(1, k.t / 0.9);
      const e = u * u * (3 - 2 * u);
      c.x = k.peak.x + (k.home.x - k.peak.x) * e;
      c.y = k.peak.y + (k.home.y - k.peak.y) * e;
      c.scale = k.peakScale + (unit - k.peakScale) * e;
      c.yaw = c.tilt.rotation.y = 0;
      animateMonster(m, c.t, { walk: 1 - u, phase: c.phase += dt * 10 });
      if (u >= 1) { c.state = 'alive'; c.mode = 'idle'; c.timer = 1; c.scale = unit; }
      this._place(c);
      return;
    }
    const enter = k.temporary ? 1.1 : 0.9; // turn + charge
    const target = V(this.w * 0.5, this.h * 0.8);
    const peakScale = unit * 3.0;
    if (k.t < enter) {
      const u = k.t / enter;
      const e = u * u;
      c.x = k.from.x + (target.x - k.from.x) * e;
      c.y = k.from.y + (target.y - k.from.y) * e;
      c.scale = k.s0 + (peakScale - k.s0) * e;
      let d = 0 - c.yaw;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      c.yaw += d * 0.6;
      c.tilt.rotation.y = c.yaw;
      c.phase += dt * 16;
      animateMonster(m, c.t, { walk: 1, phase: c.phase, excite: 0.3 });
    } else {
      if (!k.screamed) {
        k.screamed = true;
        k.onScream?.();
        this.shake = 0.9;
        this._comic(c.x, c.y - m.height * peakScale - 40, 'AAAARGH!');
      }
      c.x = target.x + (Math.random() - 0.5) * 14;
      c.scale = peakScale * (1 + Math.sin(k.t * 40) * 0.02);
      c.tilt.rotation.y = 0;
      animateMonster(m, c.t, { excite: 1, verb: 'cheer', verbW: 1, talk: 1 });
      if (k.t > enter + 1.3 && k.resolve) {
        const r = k.resolve;
        k.resolve = null;
        r();
        if (k.temporary) { this._remove(c); return; }
        c.state = 'returning';
        k.t = 0;
        k.peak = V(c.x, c.y);
        k.peakScale = c.scale;
      }
    }
    this._place(c);
  }

  _updateParticles(dt) {
    const live = [];
    for (const p of this.parts) {
      p.life -= dt;
      if (p.life <= 0) continue;
      p.vy += p.g * dt;
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.splat && p.vy > 0 && p.life < p.max * 0.55) {
        this._decal(p.x, p.y, p.size * 2.2, 0x5a0507, 0.85);
        continue;
      }
      live.push(p);
    }
    this.parts = live.slice(-1300);
    for (const pool of [this.partMeshWet, this.partMeshGlow]) pool.count = 0;
    const m4 = new THREE.Matrix4(), col = new THREE.Color();
    for (const p of this.parts) {
      const pool = p.glow ? this.partMeshGlow : this.partMeshWet;
      if (pool.count >= pool.instanceMatrix.count) continue;
      const f = p.life / p.max;
      const s = p.size * (p.grow ? 1 + (1 - f) * p.grow : 1) * Math.min(1, f * 4);
      m4.makeScale(s, s, 1).setPosition(p.x, -p.y, 9500);
      pool.setMatrixAt(pool.count, m4);
      pool.setColorAt(pool.count, col.set(p.color));
      pool.count++;
    }
    for (const pool of [this.partMeshWet, this.partMeshGlow]) {
      pool.instanceMatrix.needsUpdate = true;
      if (pool.instanceColor) pool.instanceColor.needsUpdate = true;
    }
  }
}

// Little name tag that floats above a living monster.
function makeLabel(text) {
  const cv = document.createElement('canvas');
  const g = cv.getContext('2d');
  const font = '600 26px Inter, system-ui, sans-serif';
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 30;
  cv.width = w; cv.height = 44;
  g.font = font;
  g.fillStyle = 'rgba(16,14,12,0.82)';
  g.beginPath();
  g.roundRect(1, 1, w - 2, 42, 21);
  g.fill();
  g.strokeStyle = 'rgba(166,214,90,0.7)';
  g.lineWidth = 2;
  g.stroke();
  g.fillStyle = '#efe6cf';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(text, w / 2, 23);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthTest: false }));
  mesh.renderOrder = 6;
  mesh.scale.set(w / 2, 22, 1);
  return mesh;
}

function makePartPool(scene, glow, n) {
  const mat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: glow, opacity: glow ? 0.9 : 1, blending: glow ? THREE.AdditiveBlending : THREE.NormalBlending, depthWrite: false, depthTest: false });
  const im = new THREE.InstancedMesh(circleGeo, mat, n);
  im.frustumCulled = false;
  im.count = 0;
  im.renderOrder = 4;
  im.setColorAt(0, new THREE.Color());
  scene.add(im);
  return im;
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
