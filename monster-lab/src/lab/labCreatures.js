// Wandering counterparts as real 3D actors on the laboratory floor, sharing
// the scientist's world (same floor, lights, camera). They are separate from
// the assistants they represent: cleanup kills these, never the assistants.
//
// World units are metres; the floor is y = 0.
import * as THREE from 'three';
import { setPallor, setDeadEyes, disposeMonster } from '../monsters/monsterGen.js';
import { buildFor } from '../monsters/registry.js';
import { animateMonster, hopHeight } from '../monsters/monsterAnim.js';
import { blood as bloodMat } from '../three/materials.js';

const FPS = 12;
const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const ACID = new THREE.Color(0x7aff3a);

// Free floor where monsters may roam (clear of the table, bins and machines).
const ZONES = [
  { x0: -2.6, x1: 3.5, z0: 0.8, z1: 2.1, w: 4 }, // in front of the slab
  { x0: -2.6, x1: -0.7, z0: -0.5, z1: 0.8, w: 1.4 }, // left of the scientist
  { x0: 2.75, x1: 3.5, z0: -0.25, z1: 0.8, w: 0.5 }, // right corner
];

const blobGeo = (() => {
  const g = new THREE.CircleGeometry(1, 18);
  g.rotateX(-Math.PI / 2);
  return g;
})();

export class LabCreatures {
  constructor({ scene, fx, camera }) {
    this.scene = scene;
    this.fx = fx;
    this.camera = camera;
    this.creatures = new Map();
    this.decals = [];
    this.sprites = [];
    this.t = 0;
    this._acc = 1;
    this.blobMat = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35, depthWrite: false });
    this.ray = new THREE.Raycaster();
  }

  count() {
    let n = 0;
    for (const c of this.creatures.values()) if (c.state !== 'dissolving' && !c.temporary) n++;
    return n;
  }

  // ----------------------------------------------------------- creation
  _make(rec, monster = null) {
    const m = monster || buildFor(rec.seed, rec.theme, rec.limbs);
    m.root.traverse((o) => { if (o.isMesh) { o.castShadow = false; o.receiveShadow = true; } });
    const group = new THREE.Group();
    const tilt = new THREE.Group();
    group.add(tilt);
    tilt.add(m.root);
    m.root.position.set(0, 0, 0);
    m.root.quaternion.identity();
    const blob = new THREE.Mesh(blobGeo, this.blobMat);
    blob.scale.set(m.width * 0.5, 1, m.width * 0.4);
    blob.position.y = 0.006;
    group.add(blob);
    const label = rec.name ? makeLabel(rec.name) : null;
    if (label) group.add(label);
    this.scene.add(group);
    const c = {
      id: rec.id, seed: rec.seed, defId: rec.defId || null, theme: rec.theme || null, name: rec.name || null, limbs: rec.limbs || [],
      m, group, tilt, blob, label,
      pos: V(), yaw: 0, yawT: 0, state: 'alive', mode: 'idle', timer: 1 + Math.random() * 2, target: null,
      phase: Math.random() * 6, walk: 0, excite: 0, t: Math.random() * 10, flies: [], side: 1,
    };
    this.creatures.set(c.id, c);
    return c;
  }

  restore(list) {
    for (const s of list) {
      if (s.z === undefined) continue; // records from the old 2D layer
      const c = this._make(s);
      c.pos.set(s.x, 0, s.z);
      c.yaw = c.yawT = s.yaw || 0;
      if (s.state === 'dead') this._makeCorpse(c, s.side ?? 1, true);
      this._place(c);
    }
  }

  serialize() {
    const out = [];
    for (const c of this.creatures.values()) {
      if (c.state === 'dissolving' || c.temporary) continue;
      const dead = c.state === 'dead' || c.state === 'flung';
      const p = c.state === 'arriving' ? c.arrive.to : c.state === 'summoned' || c.state === 'returning' ? c.sum.home : c.pos;
      out.push({ id: c.id, seed: c.seed, defId: c.defId, theme: c.theme, name: c.name, limbs: c.limbs, state: dead ? 'dead' : 'alive', x: +p.x.toFixed(3), z: +p.z.toFixed(3), yaw: +c.yaw.toFixed(2), side: c.side });
    }
    return out;
  }

  // The freshly made monster leaps off the slab onto the floor. Takes over the
  // patient's own monster (wounds and all). Resolves when it has landed.
  adopt(rec, patient) {
    const holder = patient.holder;
    holder.updateMatrixWorld(true);
    const from = holder.getWorldPosition(V());
    const m = patient.release();
    const c = this._make(rec, m);
    c.pos.copy(from);
    c.yaw = 0;
    const to = this._freeSpot(V(from.x + (Math.random() - 0.5) * 1.2, 0, 1.15 + Math.random() * 0.5), 0.7);
    return new Promise((resolve) => {
      c.state = 'arriving';
      c.arrive = { t: 0, from, to, dur: 0.9, resolve };
      this._place(c);
    });
  }

  _randomPoint() {
    const total = ZONES.reduce((s, z) => s + z.w, 0);
    let r = Math.random() * total;
    const z = ZONES.find((zz) => (r -= zz.w) <= 0) || ZONES[0];
    return V(z.x0 + Math.random() * (z.x1 - z.x0), 0, z.z0 + Math.random() * (z.z1 - z.z0));
  }

  _freeSpot(pref, minDist = 0.55) {
    let best = pref, tries = 0;
    const ok = (p) => [...this.creatures.values()].every((c) => c.state === 'dissolving' || c.pos.distanceTo(p) > minDist);
    while (!ok(best) && tries++ < 20) best = tries < 6 ? pref.clone().add(V((Math.random() - 0.5) * 1.2, 0, (Math.random() - 0.5) * 0.8)) : this._randomPoint();
    const zf = ZONES[0];
    best.x = THREE.MathUtils.clamp(best.x, zf.x0, zf.x1);
    best.z = THREE.MathUtils.clamp(best.z, -0.5, zf.z1);
    return best;
  }

  // ------------------------------------------------------------ summon
  // An existing assistant answers a lab question: its counterpart turns to
  // the viewer, charges at the camera and screams. If it was destroyed, a
  // temporary stand-in walks in from the side for the transition.
  summon(rec, { onScream, target: at } = {}) {
    return new Promise((resolve) => {
      let c = [...this.creatures.values()].find((x) => x.defId === rec.defId && x.state === 'alive');
      let temporary = false;
      if (!c) {
        c = this._make({ ...rec, id: `tmp-${rec.defId}-${Date.now()}` });
        c.pos.set(Math.random() < 0.5 ? -3.0 : 3.8, 0, 1.6);
        c.temporary = temporary = true;
      }
      let target = at?.clone();
      if (!target) {
        const fwd = V(0, 0, -1).applyQuaternion(this.camera.quaternion).setY(0).normalize();
        target = this.camera.position.clone().addScaledVector(fwd, 1.3 + c.m.height * 0.6).setY(0);
      }
      c.state = 'summoned';
      c.sum = { t: 0, from: c.pos.clone(), home: c.pos.clone(), target, onScream, resolve, temporary, screamed: false };
      this._place(c);
    });
  }

  // ------------------------------------------------------------ deaths
  _makeCorpse(c, side, instant = false) {
    c.state = 'dead';
    c.side = side;
    setPallor(c.m, 0.55);
    setDeadEyes(c.m, true);
    c.tilt.rotation.set(0, 0, side * Math.PI / 2 * 0.94);
    c.tilt.position.y = c.m.width * 0.42;
    for (const f of c.flies) c.group.remove(f.mesh);
    c.flies = [];
    c.pool = this._decal(c.pos.clone(), c.m.height * 0.45, 0x4a0306, instant ? 1 : 0.1);
    for (let i = 0; i < 2; i++) {
      const f = new THREE.Mesh(new THREE.SphereGeometry(0.008, 5, 4), new THREE.MeshBasicMaterial({ color: 0x0a0a0a }));
      c.group.add(f);
      c.flies.push({ mesh: f, a: Math.random() * 6, r: 0.18 + Math.random() * 0.1 });
    }
    if (c.label) c.label.visible = false;
  }

  _remove(c) {
    this.scene.remove(c.group);
    if (c.label) { c.label.material.map.dispose(); c.label.material.dispose(); }
    disposeMonster(c.m);
    this.creatures.delete(c.id);
  }

  clear() {
    for (const c of [...this.creatures.values()]) this._remove(c);
    for (const d of this.decals) this.scene.remove(d.mesh);
    for (const s of this.sprites) this.scene.remove(s.sprite);
    this.decals = []; this.sprites = [];
  }

  // Bomb lands at `at`: chain-reaction blasts kill every counterpart.
  explodeAt(at) {
    const list = [...this.creatures.values()].filter((c) => c.state !== 'dissolving').sort((a, b) => a.pos.distanceTo(at) - b.pos.distanceTo(at));
    this.fx.fire(at.clone().setY(0.3), 70);
    this.fx.smoke(at.clone().setY(0.4), 18, 0x3a3630);
    this._decal(at.clone(), 0.9, 0x120d08, 1);
    this._comic(at.clone().setY(1.3), 'KA-BLAM!', 1.1);
    list.forEach((c, i) => setTimeout(() => {
      if (!this.creatures.has(c.id)) return;
      const p = c.pos.clone().setY(0.4);
      this.fx.fire(p, 26);
      this.fx.blood(p, V(0, 1, 0), 26, 2.4, 3);
      this._fling(c, at);
    }, 120 + i * 90));
    if (list.length > 3) setTimeout(() => this._comic(list[list.length - 1].pos.clone().setY(1.2), 'SPLORCH!', 0.8), 400);
    return wait(1300 + list.length * 90);
  }

  _fling(c, from) {
    const away = c.pos.clone().sub(from).setY(0);
    if (away.lengthSq() < 0.01) away.set(Math.random() - 0.5, 0, Math.random() - 0.5);
    away.normalize().multiplyScalar(1.2 + Math.random() * 1.5);
    c.state = 'flung';
    c.fling = { v: V(away.x, 3.2 + Math.random() * 1.5, away.z), spin: V((Math.random() - 0.5) * 12, (Math.random() - 0.5) * 6, (Math.random() - 0.5) * 12) };
    if (c.pool) { c.pool.fade = true; c.pool = null; }
    c.tilt.position.y = 0;
    setPallor(c.m, 0.4, new THREE.Color(0x2a2018));
    setDeadEyes(c.m, true);
    if (c.label) c.label.visible = false;
  }

  // Acid: green rain, then every corpse melts into a puddle.
  dissolveAll() {
    for (let i = 0; i < 6; i++) this.fx.goo(V(-1.5 + i * 0.9, 2.8, 1.2 + (i % 2) * 0.4), 14, 0.6);
    const list = [...this.creatures.values()];
    list.forEach((c, i) => setTimeout(() => {
      if (!this.creatures.has(c.id)) return;
      if (c.state === 'flung') { c.pos.y = 0; this._makeCorpse(c, c.side || 1, true); }
      c.state = 'dissolving';
      c.dis = { t: 0 };
      this._decal(c.pos.clone(), c.m.height * 0.6, 0x4aaa1a, 0.15);
    }, 400 + i * 110));
    return wait(400 + list.length * 110 + 1900);
  }

  // The hose jet lands at `p`: stains (and anything left over) within r wash away.
  washAt(p, r = 0.5) {
    for (const d of this.decals) if (!d.fade && Math.hypot(d.pos.x - p.x, d.pos.z - p.z) < r + d.r * 0.5) d.fade = true;
    for (const s of this.sprites) s.fade = true;
    for (const c of [...this.creatures.values()]) {
      if ((c.state === 'dead' || c.state === 'dissolving') && Math.hypot(c.pos.x - p.x, c.pos.z - p.z) < r) this._remove(c);
    }
  }

  // ------------------------------------------------------------ picking
  // Living monster under a client-space point, given the stage rect.
  pick(clientX, clientY, rect) {
    const ndc = new THREE.Vector2(((clientX - rect.x) / rect.w) * 2 - 1, -((clientY - rect.y) / rect.h) * 2 + 1);
    this.ray.setFromCamera(ndc, this.camera);
    let best = null, bestD = Infinity;
    for (const c of this.creatures.values()) {
      if (c.state !== 'alive' || !c.defId) continue;
      const h = c.m.height;
      const center = c.pos.clone().add(V(0, h * 0.5, 0));
      const r = Math.max(h, c.m.width) * 0.55;
      const labelPos = c.pos.clone().add(V(0, h + 0.16, 0));
      const hit = this.ray.ray.distanceToPoint(center) < r || this.ray.ray.distanceToPoint(labelPos) < 0.14;
      const d = this.camera.position.distanceTo(center);
      if (hit && d < bestD) { best = c; bestD = d; }
    }
    return best;
  }

  // ------------------------------------------------------------- decals
  _decal(pos, r, color, startScale = 1) {
    const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.2, transparent: true, opacity: 0.92, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 });
    const m = new THREE.Mesh(blobGeo, mat);
    const d = { mesh: m, pos: pos.clone().setY(0.004 + Math.random() * 0.002), r, s: startScale, fade: false, sx: 0.8 + Math.random() * 0.4 };
    m.position.copy(d.pos);
    m.scale.set(r * d.s * d.sx, 1, r * d.s);
    m.receiveShadow = true;
    this.scene.add(m);
    this.decals.push(d);
    if (this.decals.length > 60) { const old = this.decals.shift(); this.scene.remove(old.mesh); old.mesh.material.dispose(); }
    return d;
  }

  _comic(pos, text, size = 1) {
    const cv = document.createElement('canvas');
    cv.width = 512; cv.height = 320;
    const g = cv.getContext('2d');
    g.translate(256, 160);
    g.beginPath();
    for (let i = 0; i < 28; i++) {
      const a = (i / 28) * Math.PI * 2, rr = i % 2 ? 110 : 160 + Math.random() * 40;
      g.lineTo(Math.cos(a) * rr * 1.4, Math.sin(a) * rr * 0.85);
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
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
    sprite.renderOrder = 20;
    sprite.position.copy(pos);
    this.scene.add(sprite);
    this.sprites.push({ sprite, t: 0, life: 1.5, size });
  }

  // --------------------------------------------------------------- frame
  _place(c) {
    const hop = c.state === 'alive' ? hopHeight(c.m, c.phase, c.walk) : 0;
    c.group.position.set(c.pos.x, c.pos.y + hop, c.pos.z);
    c.group.rotation.y = c.yaw;
    c.blob.visible = c.state !== 'flung';
    c.blob.position.y = 0.006 - c.pos.y - hop;
    if (c.label) {
      c.label.visible = c.state === 'alive' || c.state === 'returning';
      c.label.position.set(0, c.m.height + 0.16 - hop, 0);
    }
  }

  update(dt) {
    this.t += dt;
    for (const d of this.decals) {
      if (d.s < 1) d.s = Math.min(1, d.s + dt * 0.6);
      if (d.fade) d.mesh.material.opacity -= dt * 2;
      d.mesh.scale.set(d.r * d.s * d.sx, 1, d.r * d.s);
    }
    this.decals = this.decals.filter((d) => {
      if (d.mesh.material.opacity > 0) return true;
      this.scene.remove(d.mesh); d.mesh.material.dispose();
      return false;
    });
    this.sprites = this.sprites.filter((s) => {
      s.t += dt;
      const pop = Math.min(1, s.t / 0.15);
      const sc = (0.5 + pop * 0.5) * s.size;
      s.sprite.scale.set(1.6 * sc, 1.0 * sc, 1);
      if (s.t > s.life || s.fade) s.sprite.material.opacity -= dt * 4;
      if (s.sprite.material.opacity <= 0) { this.scene.remove(s.sprite); s.sprite.material.map.dispose(); return false; }
      return true;
    });
    // Smooth physics for flung bodies.
    for (const c of this.creatures.values()) {
      if (c.state !== 'flung') continue;
      const f = c.fling;
      f.v.y -= 9.8 * dt;
      c.pos.addScaledVector(f.v, dt);
      c.tilt.rotation.x += f.spin.x * dt; c.tilt.rotation.z += f.spin.z * dt; c.yaw += f.spin.y * dt;
      c.pos.x = THREE.MathUtils.clamp(c.pos.x, -3.0, 3.8);
      c.pos.z = THREE.MathUtils.clamp(c.pos.z, -1.2, 2.6);
      if (c.pos.y <= 0 && f.v.y < 0) {
        c.pos.y = 0;
        c.tilt.rotation.set(0, 0, 0);
        this.fx.blood(c.pos.clone().setY(0.1), V(0, 1, 0), 10, 1.5, 1.5);
        this._makeCorpse(c, Math.random() < 0.5 ? -1 : 1);
      }
      this._place(c);
    }
    // Stop-motion tick for the puppets.
    this._acc += dt;
    if (this._acc < 1 / FPS) return;
    const step = Math.min(this._acc, 0.25);
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
      c.pos.lerpVectors(a.from, a.to, u);
      c.pos.y = a.from.y * (1 - u) + Math.sin(u * Math.PI) * 0.6;
      c.yaw = Math.atan2(a.to.x - a.from.x, a.to.z - a.from.z) * (1 - u);
      animateMonster(m, c.t, { excite: 1 - u * 0.5 });
      if (u >= 1) {
        c.pos.y = 0;
        c.state = 'alive'; c.mode = 'idle'; c.timer = 0.8;
        this.fx.smoke(c.pos.clone().setY(0.05), 6, 0x6a6050);
        a.resolve?.();
      }
      this._place(c);
      return;
    }
    if (c.state === 'alive') {
      if (c.mode === 'walk') {
        const d = c.target.clone().sub(c.pos).setY(0);
        const dist = d.length();
        const speed = 0.32 * m.speed;
        const moving = m.gait !== 'hop' || Math.sin(c.phase) > 0;
        if (dist < 0.04) { c.mode = 'idle'; c.timer = 1 + Math.random() * 3.5; }
        else if (moving) c.pos.addScaledVector(d.normalize(), Math.min(dist, speed * dt * (m.gait === 'hop' ? 2 : 1)));
        c.yawT = Math.atan2(c.target.x - c.pos.x, c.target.z - c.pos.z);
        c.walk = Math.min(1, c.walk + dt * 4);
        c.phase += dt * (m.gait === 'scuttle' ? 13 : 8) * m.speed;
      } else {
        c.walk = Math.max(0, c.walk - dt * 4);
        c.timer -= dt;
        if (c.timer < 0) {
          c.target = this._freeSpot(this._randomPoint(), 0.45);
          c.mode = 'walk';
          c.excite = Math.random() < 0.2 ? 1 : 0;
        }
        // Idle monsters mostly face the viewer, with the odd glance around.
        if (Math.random() < 0.03) c.yawT = (Math.random() - 0.5) * 1.6;
      }
      c.excite = Math.max(0, c.excite - dt * 0.8);
      let dy = c.yawT - c.yaw;
      dy = Math.atan2(Math.sin(dy), Math.cos(dy));
      c.yaw += dy * 0.35;
      animateMonster(m, c.t, { walk: c.walk, phase: c.phase, excite: c.excite });
      this._place(c);
      return;
    }
    if (c.state === 'summoned' || c.state === 'returning') { this._tickSummon(c, dt); return; }
    if (c.state === 'dead') {
      for (const f of c.flies) {
        f.a += dt * (3 + Math.random() * 2);
        f.mesh.position.set(Math.cos(f.a) * f.r, 0.3 + Math.sin(f.a * 1.7) * 0.08, Math.sin(f.a) * f.r);
      }
      return;
    }
    if (c.state === 'dissolving') {
      const k = c.dis;
      k.t += dt;
      const u = Math.min(1, k.t / 1.7);
      setPallor(m, Math.min(1, u * 1.5), ACID);
      c.tilt.scale.set(1 + u * 0.35, Math.max(0.05, 1 - u), 1 + u * 0.35);
      for (const f of c.flies) f.mesh.visible = false;
      if (Math.random() < 0.6) this.fx.goo(c.pos.clone().setY(0.1), 2, 0.3);
      if (u >= 1) this._remove(c);
    }
  }

  _tickSummon(c, dt) {
    const k = c.sum, m = c.m;
    k.t += dt;
    if (c.state === 'returning') {
      const u = Math.min(1, k.t / 1.2);
      c.pos.lerpVectors(k.peak, k.home, u * u * (3 - 2 * u));
      c.yaw = Math.atan2(k.home.x - k.peak.x, k.home.z - k.peak.z);
      c.phase += dt * 10;
      animateMonster(m, c.t, { walk: 1 - u, phase: c.phase });
      if (u >= 1) { c.state = 'alive'; c.mode = 'idle'; c.timer = 1; c.yawT = 0; }
      this._place(c);
      return;
    }
    const charge = k.temporary ? 1.6 : 1.1;
    const toCam = Math.atan2(this.camera.position.x - c.pos.x, this.camera.position.z - c.pos.z);
    if (k.t < charge) {
      const u = k.t / charge;
      c.pos.lerpVectors(k.from, k.target, u * u);
      let dy = toCam - c.yaw;
      dy = Math.atan2(Math.sin(dy), Math.cos(dy));
      c.yaw += dy * 0.5;
      c.phase += dt * 16;
      animateMonster(m, c.t, { walk: 1, phase: c.phase, excite: 0.3 });
    } else {
      if (!k.screamed) {
        k.screamed = true;
        k.onScream?.();
        this._comic(c.pos.clone().add(V(0, m.height + 0.35, 0)), 'AAAARGH!', 0.55);
      }
      c.yaw = toCam;
      c.group.position.x = c.pos.x + (Math.random() - 0.5) * 0.02;
      animateMonster(m, c.t, { excite: 1, verb: 'cheer', verbW: 1, talk: 1 });
      if (k.t > charge + 1.3 && k.resolve) {
        const r = k.resolve;
        k.resolve = null;
        r();
        if (k.temporary) { this._remove(c); return; }
        c.state = 'returning';
        k.t = 0;
        k.peak = c.pos.clone();
      }
      return;
    }
    this._place(c);
  }
}

// Name tag floating above a living monster.
function makeLabel(text) {
  const cv = document.createElement('canvas');
  const g = cv.getContext('2d');
  const font = '600 30px Inter, system-ui, sans-serif';
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 34;
  cv.width = w; cv.height = 50;
  g.font = font;
  g.fillStyle = 'rgba(16,14,12,0.82)';
  g.beginPath();
  g.roundRect(1, 1, w - 2, 48, 24);
  g.fill();
  g.strokeStyle = 'rgba(166,214,90,0.75)';
  g.lineWidth = 2.5;
  g.stroke();
  g.fillStyle = '#efe6cf';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(text, w / 2, 26);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  s.renderOrder = 15;
  const hgt = 0.1;
  s.scale.set(hgt * (w / 50), hgt, 1);
  return s;
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export { bloodMat };
