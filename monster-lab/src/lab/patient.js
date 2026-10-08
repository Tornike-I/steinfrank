// The body on the operating table. It is the next monster, built from the same
// seed the creature layer will later use, lying inert with limbs detached.
// Exposes surgical "sites" (incision lines, sockets) in world space for the
// scientist's IK targets, and grows wounds/stitches as the operation proceeds.
import * as THREE from 'three';
import { setPallor, disposeMonster } from '../monsters/monsterGen.js';
import { buildFor } from '../monsters/registry.js';
import { enableLimbs } from '../monsters/limbParts.js';
import { clay, blood } from '../three/materials.js';
import { lumpy, mesh, stitches, sausage } from '../three/geom.js';
import { SPOTS } from './constants.js';
import { Rng } from '../core/rng.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const Q_LYING = new THREE.Quaternion().setFromEuler(new THREE.Euler(-Math.PI / 2, Math.PI / 2, 0, 'YXZ'));

export class Patient {
  // def: a monster definition ({ seed, theme, ... }) so the body on the slab
  // is the very monster that will climb off it. Tool limbs are not built in:
  // Frankenstein delivers them mid-surgery (see deliverLimb).
  constructor(def, scene) {
    const seed = def.seed;
    this.seed = seed;
    this.scene = scene;
    this.rng = new Rng(seed ^ 0x5bd1e995);
    this.m = buildFor({ ...def, limbs: [] });
    this.holder = new THREE.Group();
    this.holder.add(this.m.root);
    scene.add(this.holder);
    this.m.root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    this.bloodMat = blood();
    this.layDown();
    setPallor(this.m, 0.7);
    this.closeEyes(1);
    this.holder.updateMatrixWorld(true);
    this.wounds = [];
    this._lineIndex = 0;
    this._detach();
  }

  get height() { return this.m.height; }

  lyingTransform() {
    const H = this.m.height;
    return {
      pos: V(SPOTS.table.x + H / 2, SPOTS.tableTop + this.m.back * 0.92, SPOTS.table.z),
      quat: Q_LYING.clone(),
    };
  }
  standingTransform() {
    return { pos: V(SPOTS.table.x, SPOTS.tableTop, SPOTS.table.z + 0.05), quat: new THREE.Quaternion() };
  }
  layDown() {
    const t = this.lyingTransform();
    this.holder.position.copy(t.pos);
    this.holder.quaternion.copy(t.quat);
  }

  closeEyes(f) {
    for (const e of this.m.eyes) e.lid.rotation.x = THREE.MathUtils.lerp(e.open, Math.PI / 2, f);
  }

  // ---------------------------------------------------------- dismemberment
  _detach() {
    const parts = [...this.m.arms, ...this.m.legs.filter(() => this.m.legs.length > 1)];
    const n = Math.min(parts.length, this.rng.int(1, 2));
    this.detached = [];
    const shuffled = parts.sort(() => this.rng.next() - 0.5).slice(0, n);
    shuffled.forEach((part, i) => {
      const obj = part.pivot;
      const parent = obj.parent;
      const rest = { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() };
      // Bloody stump on the body where it was.
      const stump = mesh(new THREE.SphereGeometry(0.05, 10, 8), this.bloodMat);
      stump.position.copy(rest.pos);
      stump.scale.set(1, 0.5, 1);
      parent.add(stump);
      this.scene.attach(obj);
      // Lie it on the slab in front of the body (camera side).
      // Laid out at the ends of the slab, beyond the head and the feet.
      const side = i === 0 ? -1 : 1;
      const x = SPOTS.table.x + side * (this.m.height / 2 + 0.12);
      obj.position.set(THREE.MathUtils.clamp(x, SPOTS.table.x - 0.85, SPOTS.table.x + 0.85), SPOTS.tableTop + 0.05, SPOTS.table.z + this.rng.float(-0.05, 0.12));
      obj.quaternion.setFromUnitVectors(V(0, -1, 0), V(this.rng.float(-0.3, 0.3), 0, 1).normalize());
      const cap = mesh(new THREE.SphereGeometry(0.045, 10, 8), this.bloodMat);
      cap.scale.set(1, 0.4, 1);
      obj.add(cap);
      this.detached.push({ obj, parent, rest, stump, cap, part, attached: false });
    });
  }

  nextDetached() { return this.detached.find((d) => !d.attached) || null; }

  // A Frankenstein limb (tool) arrives: mount it at its socket, then take it
  // off again and lay it on the slab's front edge so the scientist can sew it
  // on. Returns the detached entry (its obj starts above the table for the drop).
  deliverLimb(name) {
    const m = this.m;
    const M = (color, o) => { const mat = clay(color, o); m.mats.push(mat); return mat; };
    const [part] = enableLimbs(m, [name], M);
    if (!part) return null;
    this.holder.updateMatrixWorld(true);
    const obj = part.mount;
    const parent = obj.parent;
    const rest = { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() };
    const stump = mesh(new THREE.SphereGeometry(0.045, 10, 8), this.bloodMat);
    stump.position.copy(rest.pos);
    stump.scale.set(1, 0.5, 1);
    parent.add(stump);
    this.scene.attach(obj);
    // Delivered to the ends of the slab on the scientist's side, clear of the
    // body and within easy reach, rather than at the camera-side edge.
    const i = this.detached.filter((x) => x.tool).length;
    const side = i % 2 ? 1 : -1;
    const x = THREE.MathUtils.clamp(SPOTS.table.x + side * (this.m.height / 2 + 0.16), SPOTS.table.x - 0.86, SPOTS.table.x + 0.86);
    obj.position.set(x, SPOTS.tableTop + 0.05, SPOTS.table.z - 0.2 + Math.floor(i / 2) * 0.22);
    obj.quaternion.setFromUnitVectors(V(0, 1, 0), V(-side, 0.1, this.rng.float(-0.3, 0.3)).normalize());
    const cap = mesh(new THREE.SphereGeometry(0.035, 10, 8), this.bloodMat);
    cap.scale.set(1, 0.4, 1);
    obj.add(cap);
    const d = { obj, parent, rest, stump, cap, part, attached: false, tool: name };
    this.detached.push(d);
    return d;
  }

  // Hand the finished monster over (it walks off the slab with its wounds).
  release() {
    this.scene.remove(this.holder);
    for (const d of this.detached) if (!d.attached) this.scene.remove(d.obj);
    this.holder.remove(this.m.root);
    this.released = true;
    return this.m;
  }

  socketWorld(d) {
    const m = new THREE.Matrix4().compose(d.rest.pos, d.rest.quat, d.rest.scale);
    d.parent.updateMatrixWorld(true);
    m.premultiply(d.parent.matrixWorld);
    const pos = V(), quat = new THREE.Quaternion(), sc = V();
    m.decompose(pos, quat, sc);
    return { pos, quat };
  }

  reattach(d) {
    d.parent.add(d.obj);
    d.obj.position.copy(d.rest.pos);
    d.obj.quaternion.copy(d.rest.quat);
    d.obj.scale.copy(d.rest.scale);
    d.obj.remove(d.cap);
    d.attached = true;
    d.stump.visible = false;
    // Sew a ring where it joins.
    const w = this.socketWorld(d);
    const n = V(0, 1, 0).applyQuaternion(w.quat);
    const local = d.parent.worldToLocal(w.pos.clone());
    const nl = n.clone().transformDirection(new THREE.Matrix4().copy(d.parent.matrixWorld).invert());
    const ringPts = [];
    const u = V(1, 0, 0); if (Math.abs(nl.x) > 0.9) u.set(0, 0, 1);
    const v = V().crossVectors(nl, u).normalize(); u.crossVectors(v, nl).normalize();
    for (let i = 0; i < 9; i++) {
      const a = (i / 9) * Math.PI * 2;
      const dir = u.clone().multiplyScalar(Math.cos(a)).addScaledVector(v, Math.sin(a));
      ringPts.push({ p: local.clone().addScaledVector(dir, 0.055).addScaledVector(nl, -0.02), n: dir, t: nl.clone().cross(dir) });
    }
    d.parent.add(stitches(ringPts, { len: 0.035 }));
  }

  // ------------------------------------------------------------ incisions
  // Returns world-space samples along a line on the torso surface (raycast).
  _lineSamples(fromDir, toDir, n = 12) {
    const m = this.m;
    m.torso.updateMatrixWorld(true);
    const ray = new THREE.Raycaster();
    const out = [];
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      const d = fromDir.clone().lerp(toDir, t).normalize();
      const local = V(d.x * m.torsoRadii.x, d.y * m.torsoRadii.y, d.z * m.torsoRadii.z).multiplyScalar(1.6);
      const outside = m.torso.localToWorld(local.clone());
      const center = m.torso.localToWorld(V());
      ray.set(outside, center.clone().sub(outside).normalize());
      const hit = ray.intersectObject(m.torso, false)[0];
      const p = hit ? hit.point : m.torso.localToWorld(local.multiplyScalar(1 / 1.6));
      const nrm = hit ? hit.face.normal.clone().transformDirection(m.torso.matrixWorld) : outside.clone().sub(center).normalize();
      out.push({ p, n: nrm });
    }
    for (let i = 0; i < n; i++) {
      const a = out[Math.max(0, i - 1)].p, b = out[Math.min(n - 1, i + 1)].p;
      out[i].t = b.clone().sub(a).normalize();
    }
    return out;
  }

  // Creates the next wound (unopened). The scientist's incise action cuts it.
  planIncision() {
    const lines = [
      [V(0, 0.75, 1), V(0, -0.55, 1)],
      [V(-0.65, 0.45, 1), V(0.45, -0.35, 1)],
      [V(0.65, 0.4, 1), V(-0.35, -0.45, 1)],
      [V(-0.7, -0.2, 1), V(0.7, -0.2, 1)],
    ];
    const [a, b] = lines[this._lineIndex++ % lines.length];
    const jitter = () => V(this.rng.float(-0.1, 0.1), this.rng.float(-0.1, 0.1), 0);
    const samples = this._lineSamples(a.clone().add(jitter()), b.clone().add(jitter()));
    const w = new Wound(this, samples);
    this.wounds.push(w);
    return w;
  }

  openWound() { return this.wounds.find((w) => w.open > 0.5 && !w.closed) || null; }
  anyWound() { return this.wounds[this.wounds.length - 1] || null; }

  dispose() {
    if (this.released) return;
    this.scene.remove(this.holder);
    for (const d of this.detached) if (!d.attached) this.scene.remove(d.obj);
    disposeMonster(this.m);
  }
}

class Wound {
  constructor(patient, worldSamples) {
    this.patient = patient;
    const body = patient.m.body;
    body.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(body.matrixWorld).invert();
    const k = patient.m.scale;
    this.world = worldSamples;
    // Wound geometry lives in body space so it travels with the monster.
    const local = worldSamples.map((s) => ({
      p: s.p.clone().addScaledVector(s.n, 0.004).applyMatrix4(inv),
      n: s.n.clone().transformDirection(inv),
      t: s.t.clone().transformDirection(inv),
    }));
    this.local = local;
    const curve = new THREE.CatmullRomCurve3(local.map((s) => s.p));
    const segs = 40;
    this.tube = mesh(new THREE.TubeGeometry(curve, segs, 0.011 / k, 6, false), patient.bloodMat, { cast: false });
    this.tube.geometry.setDrawRange(0, 0);
    this._perSeg = 6 * 6;
    this._segs = segs;
    body.add(this.tube);

    // Gaping interior with organs peeking out.
    const mid = local[Math.floor(local.length / 2)];
    const len = local[0].p.distanceTo(local[local.length - 1].p);
    this.gape = new THREE.Group();
    this.gape.position.copy(mid.p).addScaledVector(mid.n, -0.012 / k);
    const basis = new THREE.Matrix4().makeBasis(
      mid.t.clone(), mid.n.clone(), mid.t.clone().cross(mid.n).normalize(),
    );
    this.gape.quaternion.setFromRotationMatrix(basis);
    const hole = mesh(new THREE.SphereGeometry(1, 16, 10), new THREE.MeshStandardMaterial({ color: 0x2a0204, roughness: 0.3 }), { cast: false });
    hole.scale.set(len * 0.42, 0.02 / k, 0.045 / k);
    this.gape.add(hole);
    const gutMat = clay(0xd9707a, { rough: 0.25, bump: 3 });
    this.organs = new THREE.Group();
    for (let i = 0; i < 4; i++) {
      const g = mesh(lumpy(sausage(len * 0.35, [0.02, 0.028, 0.022].map((r) => r / k)), 0.004 / k, 30, i), gutMat, { cast: false });
      g.rotation.set(0, i * 0.8, Math.PI / 2 + (i - 1.5) * 0.4);
      g.position.set((i - 1.5) * len * 0.18, 0.01 / k, 0);
      this.organs.add(g);
    }
    this.gape.add(this.organs);
    this.gape.scale.set(1, 1, 0.001);
    this.gape.visible = false;
    body.add(this.gape);

    this.stitchMesh = stitches(local, { len: 0.05 / k, thick: 0.007 / k, cross: true, maxCount: local.length * 2 });
    this.stitchMesh.count = 0;
    body.add(this.stitchMesh);
    this.stitchesDone = 0;
    this.cut = 0;
    this.open = 0;
    this.closed = false;
  }

  pointAt(t) {
    const s = this.world;
    const f = Math.min(s.length - 1.001, Math.max(0, t * (s.length - 1)));
    const i = Math.floor(f);
    return s[i].p.clone().lerp(s[i + 1].p, f - i);
  }
  normalAt(t) { return this.world[Math.round(t * (this.world.length - 1))].n.clone(); }
  get mid() { return this.pointAt(0.5); }
  get dir() { return this.world[this.world.length - 1].p.clone().sub(this.world[0].p).normalize(); }

  setCut(f) {
    this.cut = Math.max(this.cut, f);
    this.tube.geometry.setDrawRange(0, Math.floor(this._segs * this.cut) * this._perSeg);
  }
  setOpen(f) {
    this.open = f;
    this.gape.visible = f > 0.01;
    this.gape.scale.set(1, 1, Math.max(0.001, f));
    this.organs.position.y = (f - 0.6) * 0.03;
  }
  addStitch() {
    const total = this.local.length;
    if (this.stitchesDone >= total) return false;
    this.stitchesDone++;
    this.stitchMesh.count = this.stitchesDone * 2;
    this.setOpen(Math.max(0, 1 - this.stitchesDone / total) * this.open);
    if (this.stitchesDone >= total) { this.closed = true; this.setOpen(0); }
    return true;
  }
  stitchPoint(i) {
    const total = this.local.length;
    return this.world[Math.min(total - 1, i)].p.clone();
  }
}
