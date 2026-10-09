// The Library's roaming hall: every monster you've built wanders a long room,
// fliers drift around overhead, and clicking one opens its tab.
import * as THREE from 'three';
import { CameraRig } from '../lab/labScene.js';
import { textures } from '../three/textures.js';
import { clay, metal } from '../three/materials.js';
import { mesh } from '../three/geom.js';
import { buildFor, titleOf } from '../monsters/registry.js';
import { disposeMonster } from '../monsters/monsterGen.js';
import { animateMonster, hopHeight } from '../monsters/monsterAnim.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const HALF_W = 3.6, NEAR_Z = 1.6, FAR_Z = -2.4;

export class Menagerie {
  constructor() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d0f0a);
    this.scene.fog = new THREE.Fog(0x0d0f0a, 7, 16);
    this.camera = new THREE.PerspectiveCamera(36, 2, 0.05, 40);
    this.rig = new CameraRig(this.camera);
    this.rig.set({ target: V(0, 0.75, 0), dir: V(0, 0.22, 1), fitH: 3.1, fov: 36 }, { cut: true });
    this.t = 0;
    this.mons = new Map(); // def id → creature
    this.raycaster = new THREE.Raycaster();
    this._room();
  }

  _room() {
    const t = textures();
    const s = this.scene;
    const floor = mesh(new THREE.PlaneGeometry(14, 9), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.75, bumpMap: t.clay, bumpScale: 1 }), { cast: false });
    floor.rotation.x = -Math.PI / 2;
    s.add(floor);
    const wallMat = new THREE.MeshStandardMaterial({ map: t.wall, roughness: 0.95, bumpMap: t.clay, bumpScale: 3 });
    const wall = mesh(new THREE.PlaneGeometry(14, 6), wallMat, { cast: false });
    wall.position.set(0, 3, -3.2);
    s.add(wall);
    for (const sx of [-1, 1]) {
      const side = mesh(new THREE.PlaneGeometry(9, 6), wallMat, { cast: false });
      side.rotation.y = -sx * Math.PI / 2;
      side.position.set(sx * 6, 3, 1);
      s.add(side);
    }
    const wains = mesh(new THREE.BoxGeometry(14, 1.0, 0.06), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.5 }), { cast: false });
    wains.position.set(0, 0.5, -3.17);
    s.add(wains);
    for (let i = 0; i < 3; i++) {
      const pipe = mesh(new THREE.CylinderGeometry(0.04 + i * 0.012, 0.04 + i * 0.012, 14, 10), metal(0x7a6a58));
      pipe.rotation.z = Math.PI / 2;
      pipe.position.set(0, 3.4 + i * 0.16, -3.08 + i * 0.03);
      s.add(pipe);
    }
    const jarMats = [0x9fe36a, 0xe3d26a, 0x6ad8e3, 0xe38a6a].map((c) => new THREE.MeshStandardMaterial({ color: c, transparent: true, opacity: 0.55, roughness: 0.1 }));
    const shelf = mesh(new THREE.BoxGeometry(10, 0.06, 0.32), clay(0x5a3a22));
    shelf.position.set(0, 1.9, -3.0);
    s.add(shelf);
    for (let i = 0; i < 18; i++) {
      const jar = mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.34, 12), jarMats[i % 4]);
      jar.position.set(-4.6 + i * 0.54, 2.1, -3.0);
      s.add(jar);
    }
    s.add(new THREE.HemisphereLight(0x8090a0, 0x1a140c, 0.7));
    for (const x of [-3, 0, 3]) {
      const lamp = new THREE.SpotLight(0xffe2b0, 30, 9, 0.85, 0.6, 1.5);
      lamp.position.set(x, 3.6, 0.6);
      lamp.target.position.set(x, 0, 0);
      lamp.castShadow = x === 0;
      s.add(lamp, lamp.target);
      const shade = mesh(new THREE.ConeGeometry(0.32, 0.3, 20, 1, true), metal(0x4a5a4a, { rough: 0.5 }));
      shade.material.side = THREE.DoubleSide;
      shade.position.set(x, 3.75, 0.6);
      s.add(shade);
    }
    const rim = new THREE.DirectionalLight(0x7aa8ff, 1.2);
    rim.position.set(-3, 4, -4);
    s.add(rim);
    this.blobGeo = new THREE.CircleGeometry(1, 24);
    this.blobGeo.rotateX(-Math.PI / 2);
    this.blobMat = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35, depthWrite: false });
  }

  sync(defs) {
    const ids = new Set(defs.map((d) => d.id));
    for (const [id, c] of this.mons) {
      if (!ids.has(id)) { this.scene.remove(c.group); disposeMonster(c.m); this.mons.delete(id); }
    }
    defs.forEach((def, i) => {
      if (this.mons.has(def.id)) return;
      const m = buildFor(def);
      m.root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.userData.defId = def.id; } });
      const group = new THREE.Group();
      group.add(m.root);
      const blob = new THREE.Mesh(this.blobGeo, this.blobMat);
      blob.position.y = 0.006;
      blob.scale.setScalar(m.width * 0.45);
      group.add(blob);
      const label = makeLabel(titleOf(def));
      label.userData.labelOf = def.id;
      group.add(label);
      const flier = m.gait === 'fly';
      const pos = V((Math.random() * 2 - 1) * HALF_W * 0.8, flier ? 0.7 + Math.random() * 0.6 : 0, FAR_Z + 0.4 + Math.random() * (NEAR_Z - FAR_Z - 0.8));
      group.position.copy(pos);
      this.scene.add(group);
      this.mons.set(def.id, {
        def, m, group, blob, label, flier, pos, target: pos.clone(), yaw: Math.random() * 6, walk: 0, phase: Math.random() * 6,
        idle: 0.5 + Math.random() * 2 + i * 0.3, hover: false,
      });
    });
  }

  _newTarget(c) {
    const x = (Math.random() * 2 - 1) * HALF_W;
    const z = FAR_Z + 0.3 + Math.random() * (NEAR_Z - FAR_Z - 0.6);
    const y = c.flier ? 0.6 + Math.random() * 0.9 : 0;
    c.target.set(x, y, z);
  }

  pick(x, y, rect) {
    const ndc = new THREE.Vector2(((x - rect.x) / rect.w) * 2 - 1, -((y - rect.y) / rect.h) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const roots = [...this.mons.values()].map((c) => c.group);
    const hit = this.raycaster.intersectObjects(roots, true).find((h) => h.object.userData.defId || h.object.userData.labelOf);
    const id = hit && (hit.object.userData.defId || hit.object.userData.labelOf);
    return id ? this.mons.get(id)?.def || null : null;
  }

  setHover(id) {
    for (const c of this.mons.values()) c.hover = c.def.id === id;
  }

  update(dt) {
    this.t += dt;
    for (const c of this.mons.values()) {
      const m = c.m;
      const to = c.target.clone().sub(c.pos);
      const flat = V(to.x, 0, to.z);
      const dist = c.flier ? to.length() : flat.length();
      if (c.idle > 0) {
        c.idle -= dt;
        c.walk = Math.max(0, c.walk - dt * 3);
        if (c.idle <= 0) this._newTarget(c);
      } else if (dist < 0.08) {
        c.idle = 1 + Math.random() * 3.5;
      } else {
        const speed = 0.35 * m.speed * Math.max(0.6, Math.min(1.6, m.height));
        const step = Math.min(dist, speed * dt * (c.flier ? 1.4 : 1));
        c.pos.addScaledVector(to.normalize(), step);
        c.walk = Math.min(1, c.walk + dt * 3);
        const wantYaw = Math.atan2(flat.x, flat.z);
        let d = wantYaw - c.yaw;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        c.yaw += d * Math.min(1, dt * 4);
        c.phase += dt * (m.gait === 'scuttle' ? 13 : 8) * m.speed * c.walk;
      }
      const hop = hopHeight(m, c.phase, c.walk);
      c.group.position.set(c.pos.x, c.pos.y + hop, c.pos.z);
      c.group.rotation.y = c.yaw;
      if (c.flier) c.group.rotation.z = Math.sin(this.t * 0.9 + c.phase) * 0.08;
      c.blob.position.y = 0.006 - c.pos.y - hop;
      c.blob.scale.setScalar(m.width * 0.45 / (1 + c.pos.y * 0.6));
      c.label.position.set(0, m.height + 0.14, 0);
      c.label.material.opacity = c.hover ? 1 : 0.75;
      c.label.scale.copy(c.label.userData.base).multiplyScalar(c.hover ? 1.15 : 1);
      animateMonster(m, this.t + c.phase, { walk: c.walk, phase: c.phase, excite: c.hover ? 0.6 : 0, verb: c.hover ? 'greet' : null, verbW: 1 });
    }
  }
}

function makeLabel(text) {
  const cv = document.createElement('canvas');
  const g = cv.getContext('2d');
  g.font = '700 30px Inter, system-ui, sans-serif';
  const w = Math.ceil(g.measureText(text).width) + 36;
  cv.width = w; cv.height = 50;
  g.font = '700 30px Inter, system-ui, sans-serif';
  g.fillStyle = 'rgba(20,18,15,0.85)';
  g.beginPath();
  g.roundRect(0, 0, w, 50, 25);
  g.fill();
  g.fillStyle = '#ece5d6';
  g.textBaseline = 'middle';
  g.fillText(text, 18, 27);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  s.renderOrder = 10;
  const hgt = 0.16;
  s.scale.set(hgt * (w / 50), hgt, 1);
  s.userData.base = s.scale.clone();
  s.center.set(0.5, 0);
  return s;
}
