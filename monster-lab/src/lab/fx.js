// Lab particle effects: blood, sparks, water, smoke, splats and lightning.
import * as THREE from 'three';
import { SPOTS_TABLE } from './constants.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(), _s = new THREE.Vector3(), _c = new THREE.Color();

class Pool {
  constructor(scene, mat, count) {
    this.mesh = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), mat, count);
    this.mesh.frustumCulled = false;
    this.mesh.count = 0;
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.mesh.setColorAt(0, new THREE.Color());
    scene.add(this.mesh);
    this.max = count;
    this.items = [];
  }
  add(p) { if (this.items.length < this.max) this.items.push(p); }
  update(dt, onLand) {
    const live = [];
    for (const p of this.items) {
      p.life -= dt;
      if (p.life <= 0) continue;
      p.v.y -= p.g * dt;
      p.v.multiplyScalar(1 - p.drag * dt);
      p.p.addScaledVector(p.v, dt);
      if (p.stick) {
        const floor = surfaceY(p.p);
        if (p.p.y <= floor) { onLand(p, floor); continue; }
      }
      live.push(p);
    }
    this.items = live;
    let k = 0;
    for (const p of live) {
      const f = p.life / p.max;
      const sz = p.size * (p.grow ? 1 + (1 - f) * p.grow : 1) * (p.fade ? Math.min(1, f * 3) : 1);
      const stretch = p.stretch ? 1 + Math.min(3, p.v.length() * 0.15) : 1;
      _q.setFromUnitVectors(V(0, 1, 0), p.v.lengthSq() > 1e-4 ? p.v.clone().normalize() : V(0, 1, 0));
      _m.compose(p.p, _q, _s.set(sz, sz * stretch, sz));
      this.mesh.setMatrixAt(k, _m);
      this.mesh.setColorAt(k, _c.set(p.color));
      k++;
    }
    this.mesh.count = k;
    this.mesh.instanceMatrix.needsUpdate = true;
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
  }
}

function surfaceY(p) {
  const t = SPOTS_TABLE;
  if (p.x > t.x - 1.0 && p.x < t.x + 1.0 && p.z > t.z - 0.37 && p.z < t.z + 0.37 && p.y > t.top - 0.15) return t.top + 0.003;
  return 0.006;
}

export class LabFx {
  constructor(scene) {
    this.scene = scene;
    this.wet = new Pool(scene, new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.2, metalness: 0.05 }), 500);
    this.glow = new Pool(scene, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }), 500);
    this.splatMesh = new THREE.InstancedMesh(new THREE.CircleGeometry(1, 9), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.15, polygonOffset: true, polygonOffsetFactor: -2 }), 160);
    this.splatMesh.count = 0;
    this.splatMesh.frustumCulled = false;
    this.splatMesh.setColorAt(0, new THREE.Color());
    this.splatMesh.receiveShadow = true;
    scene.add(this.splatMesh);
    this._splat = 0;
    this.bolts = [];
  }

  blood(pos, dir = V(0, 1, 0), n = 12, spread = 1.2, speed = 1.6) {
    for (let i = 0; i < n; i++) {
      const v = dir.clone().normalize().multiplyScalar(speed * (0.4 + Math.random()))
        .add(V((Math.random() - 0.5) * spread, Math.random() * spread * 0.6, (Math.random() - 0.5) * spread));
      this.wet.add({ p: pos.clone(), v, life: 2, max: 2, size: 0.006 + Math.random() * 0.012, color: Math.random() < 0.3 ? 0x9a0a0c : 0x6a0306, g: 9.8, drag: 0.4, stick: true, stretch: true });
    }
  }
  sparks(pos, n = 20, color = 0xffd27a) {
    for (let i = 0; i < n; i++) {
      const v = V(Math.random() - 0.5, Math.random() * 0.8 + 0.2, Math.random() - 0.5).multiplyScalar(2.5);
      this.glow.add({ p: pos.clone(), v, life: 0.5 + Math.random() * 0.4, max: 0.9, size: 0.008 + Math.random() * 0.01, color, g: 6, drag: 1, stretch: true, fade: true });
    }
  }
  spray(pos, dir, n = 6, color = 0x9ad0ff, speed = 6) {
    for (let i = 0; i < n; i++) {
      const v = dir.clone().normalize().multiplyScalar(speed * (0.8 + Math.random() * 0.4)).add(V((Math.random() - 0.5) * 0.6, (Math.random() - 0.5) * 0.6, (Math.random() - 0.5) * 0.6));
      // Short-lived so droplets vanish before they reach the lens.
      this.glow.add({ p: pos.clone(), v, life: 0.26, max: 0.26, size: 0.005 + Math.random() * 0.008, color, g: 3, drag: 0.3, stretch: true, fade: true });
    }
  }
  smoke(pos, n = 8, color = 0x5a5a50) {
    for (let i = 0; i < n; i++) {
      const v = V((Math.random() - 0.5) * 0.4, 0.4 + Math.random() * 0.4, (Math.random() - 0.5) * 0.4);
      this.wet.add({ p: pos.clone(), v, life: 1.4, max: 1.4, size: 0.04 + Math.random() * 0.05, color, g: -0.2, drag: 1.2, grow: 2.5, fade: true });
    }
  }

  addSplat(pos, size, color = 0x5a0204) {
    const i = this._splat++ % this.splatMesh.instanceMatrix.count;
    _q.setFromAxisAngle(V(1, 0, 0), -Math.PI / 2);
    _m.compose(pos, _q, _s.set(size * (0.7 + Math.random() * 0.6), size, 1));
    this.splatMesh.setMatrixAt(i, _m);
    this.splatMesh.setColorAt(i, _c.set(color));
    this.splatMesh.count = Math.min(this._splat, this.splatMesh.instanceMatrix.count);
    this.splatMesh.instanceMatrix.needsUpdate = true;
    this.splatMesh.instanceColor.needsUpdate = true;
  }
  clearSplats() { this._splat = 0; this.splatMesh.count = 0; }

  // Jagged electric arc, regenerated in steps. Returns a stop function.
  bolt(from, to, duration = 0.8, color = 0xbfe8ff) {
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false });
    const b = { from, to, mat, t: 0, duration, meshes: [], acc: 1 };
    this.bolts.push(b);
    return b;
  }

  _rebuildBolt(b) {
    for (const m of b.meshes) { this.scene.remove(m); m.geometry.dispose(); }
    b.meshes = [];
    for (let strand = 0; strand < 2; strand++) {
      const pts = [];
      const n = 12;
      for (let i = 0; i <= n; i++) {
        const t = i / n;
        const p = b.from.clone().lerp(b.to, t);
        if (i > 0 && i < n) p.add(V((Math.random() - 0.5) * 0.22, (Math.random() - 0.5) * 0.22, (Math.random() - 0.5) * 0.22));
        pts.push(p);
      }
      const geo = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, 'chordal'), 24, strand ? 0.006 : 0.012, 4);
      const m = new THREE.Mesh(geo, b.mat);
      this.scene.add(m);
      b.meshes.push(m);
    }
  }

  update(dt) {
    const land = (p, y) => {
      p.p.y = y;
      if (p.color === 0x6a0306 || p.color === 0x9a0a0c || p.landSplat) this.addSplat(p.p, p.size * 2.2, 0x5a0204);
    };
    this.wet.update(dt, land);
    this.glow.update(dt, () => {});
    this.bolts = this.bolts.filter((b) => {
      b.t += dt;
      b.acc += dt;
      if (b.t >= b.duration) {
        for (const m of b.meshes) { this.scene.remove(m); m.geometry.dispose(); }
        b.mat.dispose();
        return false;
      }
      if (b.acc > 1 / 20) { b.acc = 0; this._rebuildBolt(b); }
      return true;
    });
  }
}
