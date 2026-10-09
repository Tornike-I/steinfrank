// Frankenstein "limbs" are a monster's tools (web_search, llm, sokosumi, …).
// Here each limb type becomes a physical body part sewn onto the monster, and
// it animates while a workflow step using that limb is running.
//
// Parts are built in the monster's unscaled units (H = unscaled height) and
// mounted on sockets around the torso, pointing outward along +Y.
import * as THREE from 'three';
import { clay, metal, glass, glossy, rubber } from '../three/materials.js';
import { lumpy, sausage, mesh, stitches, ringSamples } from '../three/geom.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

// type → { label, verb (den performance), slot, build(H, api) → { group, animate(t, w) } }
export const LIMB_TYPES = {
  web_search: { label: 'Telescope eye', verb: 'look', slot: 'top', build: telescope },
  http_fetch: { label: 'Grabber claw', verb: 'look', slot: 'side', build: grabber },
  llm: { label: 'Brain jar', verb: 'think', slot: 'top', build: brainJar },
  forged: { label: 'Carved limb', verb: 'compute', slot: 'side', build: carved },
  sokosumi: { label: 'Hired tentacle', verb: 'read', slot: 'back', build: hiredTentacle },
  tts: { label: 'Gramophone horn', verb: 'speak', slot: 'top', build: horn },
  sfx: { label: 'Squeezebox', verb: 'speak', slot: 'back', build: bellows },
  image: { label: 'Camera eye', verb: 'look', slot: 'top', build: cameraEye },
  notify: { label: 'Alarm bell', verb: 'write', slot: 'top', build: bell },
  other: { label: 'Spare limb', verb: 'think', slot: 'side', build: spareArm },
};

// The limbs a scripted (offline) workflow implies, by step kind.
export const LIMB_FOR_KIND = { gather: 'web_search', read: 'http_fetch', think: 'llm', compute: 'forged:crunch', write: 'llm' };

export const limbKind = (name) => (name?.startsWith('forged:') ? 'forged' : LIMB_TYPES[name] ? name : 'other');
export const limbInfo = (name) => LIMB_TYPES[limbKind(name)];
export const limbLabel = (name) => {
  const info = limbInfo(name);
  return name?.startsWith('forged:') ? `${info.label} (${name.slice(7).replace(/_/g, ' ')})` : info.label;
};

// Socket directions (azimuth around y with 0 = front, elevation) per slot.
const SOCKETS = {
  top: [[0.55, 0.78], [-0.55, 0.78], [Math.PI, 0.72], [0, 0.9]],
  side: [[-1.95, 0.15], [1.95, 0.15], [-2.35, -0.25], [2.35, -0.25]],
  back: [[Math.PI, 0.25], [Math.PI - 0.6, -0.15], [Math.PI + 0.6, -0.15]],
};

// Attach every limb in `names` to the monster. Returns the mounted parts.
export function mountLimbs(m, names, M, already = 0) {
  const used = { top: 0, side: 0, back: 0 };
  for (const p of (m.limbParts || []).slice(0, already)) used[p.slot]++;
  const H = m.height / m.scale;
  const tc = m.torsoCenter, rad = m.torsoRadii;
  const parts = [];
  const seen = new Set();
  for (const name of names) {
    // One body part per distinct limb (forged limbs are distinct tools).
    if (seen.has(name)) continue;
    seen.add(name);
    const info = limbInfo(name);
    let slot = info.slot;
    if (used[slot] >= SOCKETS[slot].length) slot = ['side', 'back', 'top'].find((s) => used[s] < SOCKETS[s].length);
    if (!slot) break;
    const [az, el] = SOCKETS[slot][used[slot]++];
    const d = V(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
    const p = V(d.x * rad.x, d.y * rad.y, d.z * rad.z).multiplyScalar(0.92).add(tc);
    const n = V(d.x / rad.x, d.y / rad.y, d.z / rad.z).normalize();
    const mount = new THREE.Group();
    mount.position.copy(p);
    mount.quaternion.setFromUnitVectors(V(0, 1, 0), n);
    const built = info.build(H, { M });
    built.group.scale.setScalar(1.35); // read clearly at lab-floor distance
    mount.add(built.group);
    mount.add(stitches(ringSamples(V(0, 0.01 * H, 0), V(0, 1, 0), 0.05 * H, 9), { len: 0.03 * H }));
    m.body.add(mount);
    parts.push({ name, kind: limbKind(name), mount, animate: built.animate || (() => {}), slot });
  }
  return parts;
}

// Mount limbs and register the updater that animates whichever are active
// (m.activeLimbs holds limb names or kinds). Safe to call more than once.
export function enableLimbs(m, names, M) {
  m.limbParts ||= [];
  m.activeLimbs ||= new Set();
  const fresh = names.filter((n) => !m.limbParts.some((p) => p.name === n));
  const parts = mountLimbs(m, fresh, M, m.limbParts.length);
  m.limbParts.push(...parts);
  if (!m._limbUpdater) {
    const weights = new Map();
    m._limbUpdater = (t) => {
      for (const part of m.limbParts) {
        const on = m.activeLimbs.has(part.name) || m.activeLimbs.has(part.kind) ? 1 : 0;
        const w = (weights.get(part) ?? 0) + (on - (weights.get(part) ?? 0)) * 0.35;
        weights.set(part, w);
        part.animate(t, w);
      }
    };
    m.updaters.push(m._limbUpdater);
  }
  m.root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return parts;
}

// ------------------------------------------------------------- builders --
function stalk(H, len, r, mat) {
  const s = mesh(sausage(len, [r, r * 0.85, r * 0.9]), mat);
  s.rotation.x = Math.PI; // grow along +Y
  return s;
}

function telescope(H, { M }) {
  const g = new THREE.Group();
  g.add(stalk(H, 0.16 * H, 0.025 * H, M(0x9a8a70)));
  const tube = new THREE.Group();
  tube.position.y = 0.17 * H;
  const brass = metal(0xb8893a, { rough: 0.3 });
  for (let i = 0; i < 3; i++) {
    const t = mesh(new THREE.CylinderGeometry((0.032 - i * 0.006) * H, (0.034 - i * 0.006) * H, 0.09 * H, 12), brass);
    t.rotation.x = Math.PI / 2;
    t.position.z = i * 0.07 * H;
    tube.add(t);
  }
  const lens = mesh(new THREE.CircleGeometry(0.03 * H, 14), glossy(0x7ad8ff, { emissive: 0x2a8aff, ei: 0.3 }), { cast: false });
  lens.position.z = 0.19 * H;
  tube.add(lens);
  g.add(tube);
  return {
    group: g,
    animate(t, w) {
      tube.rotation.y = Math.sin(t * (w ? 2.5 : 0.4)) * (0.3 + w * 0.6);
      tube.rotation.x = -0.2 + Math.sin(t * 1.7) * 0.15 * w;
      lens.material.emissiveIntensity = 0.3 + w * (1 + Math.sin(t * 9));
    },
  };
}

function grabber(H, { M }) {
  const g = new THREE.Group();
  const mat = metal(0x7a7a70, { rough: 0.45 });
  const seg1 = new THREE.Group();
  seg1.add(stalk(H, 0.16 * H, 0.018 * H, mat));
  g.add(seg1);
  const seg2 = new THREE.Group();
  seg2.position.y = 0.16 * H;
  seg2.add(stalk(H, 0.14 * H, 0.015 * H, mat));
  seg1.add(seg2);
  const claw = new THREE.Group();
  claw.position.y = 0.15 * H;
  const fingers = [];
  for (let i = 0; i < 3; i++) {
    const f = new THREE.Group();
    f.rotation.y = (i / 3) * Math.PI * 2;
    const tip = mesh(new THREE.ConeGeometry(0.012 * H, 0.06 * H, 6), M(0xb04a3a, { rough: 0.5 }));
    tip.position.set(0.02 * H, 0.03 * H, 0);
    tip.rotation.z = -0.4;
    f.add(tip);
    claw.add(f);
    fingers.push(tip);
  }
  seg2.add(claw);
  return {
    group: g,
    animate(t, w) {
      seg1.rotation.z = 0.5 + Math.sin(t * 3) * 0.25 * w;
      seg2.rotation.z = -0.9 + Math.sin(t * 3 + 1) * 0.6 * w;
      for (const f of fingers) f.rotation.z = -0.4 - Math.abs(Math.sin(t * 6)) * 0.5 * w;
    },
  };
}

function brainJar(H, { M }) {
  const g = new THREE.Group();
  g.add(stalk(H, 0.06 * H, 0.03 * H, metal(0x8a8a80)));
  const jar = new THREE.Mesh(new THREE.CylinderGeometry(0.06 * H, 0.06 * H, 0.13 * H, 14), glass(0xd8f0ff, 0.28));
  jar.position.y = 0.13 * H;
  g.add(jar);
  const lid = mesh(new THREE.CylinderGeometry(0.064 * H, 0.064 * H, 0.015 * H, 14), metal(0xb8893a));
  lid.position.y = 0.2 * H;
  g.add(lid);
  const brainMat = clay(0xe48a9a, { rough: 0.35, bump: 4 });
  brainMat.emissive = new THREE.Color(0x8a2aff);
  const brain = mesh(lumpy(new THREE.SphereGeometry(0.045 * H, 14, 10), 0.008 * H, 60, 3), brainMat);
  brain.scale.set(1, 0.8, 1.1);
  brain.position.y = 0.12 * H;
  g.add(brain);
  return {
    group: g,
    animate(t, w) {
      brain.position.y = 0.12 * H + Math.sin(t * 2) * 0.006 * H;
      brainMat.emissiveIntensity = w * (0.4 + Math.abs(Math.sin(t * 7)) * 0.8);
      brain.rotation.y = t * (0.3 + w * 2);
    },
  };
}

function carved(H, { M }) {
  const g = new THREE.Group();
  const wood = clay(0x8a5a2a, { rough: 0.9, bump: 3 });
  const arm = new THREE.Group();
  arm.add(stalk(H, 0.2 * H, 0.022 * H, wood));
  for (const y of [0.06, 0.14]) {
    const bolt = mesh(new THREE.CylinderGeometry(0.008 * H, 0.008 * H, 0.06 * H, 6), metal(0x9a9a90));
    bolt.rotation.z = Math.PI / 2;
    bolt.position.y = y * H;
    arm.add(bolt);
  }
  const wrench = new THREE.Group();
  wrench.position.y = 0.21 * H;
  const handle = mesh(new THREE.BoxGeometry(0.012 * H, 0.08 * H, 0.008 * H), metal(0xb0b0a8, { rust: false }));
  handle.position.y = 0.04 * H;
  wrench.add(handle);
  const jaw = mesh(new THREE.TorusGeometry(0.018 * H, 0.007 * H, 6, 10, Math.PI * 1.5), metal(0xb0b0a8, { rust: false }));
  jaw.position.y = 0.09 * H;
  wrench.add(jaw);
  arm.add(wrench);
  g.add(arm);
  return {
    group: g,
    animate(t, w) {
      arm.rotation.z = 0.35 + Math.sin(t * 12) * 0.35 * w;
      wrench.rotation.y = t * 8 * w;
    },
  };
}

function hiredTentacle(H, { M }) {
  const g = new THREE.Group();
  const mat = M(0x8a4ab0, { rough: 0.5 });
  let parent = g, r = 0.03 * H;
  const segs = [];
  for (let i = 0; i < 4; i++) {
    const s = new THREE.Group();
    s.position.y = i ? 0.07 * H : 0;
    s.add(stalk(H, 0.075 * H, r, mat));
    parent.add(s);
    segs.push(s);
    parent = s;
    r *= 0.75;
  }
  const coin = mesh(new THREE.CylinderGeometry(0.03 * H, 0.03 * H, 0.006 * H, 16), metal(0xd8a830, { rough: 0.25, rust: false }));
  coin.position.y = 0.09 * H;
  coin.rotation.z = Math.PI / 2;
  parent.add(coin);
  const tag = mesh(new THREE.BoxGeometry(0.05 * H, 0.03 * H, 0.003 * H), clay(0xf0e8d0, { bump: 0.5 }));
  tag.position.set(0.03 * H, 0.04 * H, 0);
  segs[1].add(tag);
  return {
    group: g,
    animate(t, w) {
      segs.forEach((s, i) => { if (i) s.rotation.z = Math.sin(t * (1.5 + w * 4) + i) * (0.25 + w * 0.3); });
      coin.rotation.x = t * (1 + w * 10);
    },
  };
}

function horn(H) {
  const g = new THREE.Group();
  const brass = metal(0xc8963a, { rough: 0.25, rust: false });
  g.add(stalk(H, 0.08 * H, 0.012 * H, brass));
  const bellG = new THREE.Group();
  bellG.position.y = 0.08 * H;
  const bellM = mesh(new THREE.CylinderGeometry(0.075 * H, 0.012 * H, 0.14 * H, 18, 1, true), brass);
  bellM.material = brass.clone(); bellM.material.side = THREE.DoubleSide;
  bellM.position.y = 0.07 * H;
  bellG.add(bellM);
  bellG.rotation.x = 0.6;
  g.add(bellG);
  return { group: g, animate(t, w) { bellG.scale.setScalar(1 + Math.abs(Math.sin(t * 14)) * 0.12 * w); } };
}

function bellows(H, { M }) {
  const g = new THREE.Group();
  const box = new THREE.Group();
  box.position.y = 0.06 * H;
  const pleats = [];
  for (let i = 0; i < 4; i++) {
    const p = mesh(new THREE.BoxGeometry(0.1 * H, 0.015 * H, 0.08 * H), M(i % 2 ? 0x2a2a2a : 0xb02a2a, { rough: 0.8 }));
    box.add(p);
    pleats.push(p);
  }
  g.add(box);
  return {
    group: g,
    animate(t, w) {
      const open = 0.02 + Math.abs(Math.sin(t * 5)) * 0.02 * w;
      pleats.forEach((p, i) => { p.position.y = i * open * H; });
    },
  };
}

function cameraEye(H) {
  const g = new THREE.Group();
  g.add(stalk(H, 0.1 * H, 0.015 * H, metal(0x5a5a5a)));
  const cam = new THREE.Group();
  cam.position.y = 0.13 * H;
  cam.add(mesh(new THREE.BoxGeometry(0.08 * H, 0.06 * H, 0.06 * H), clay(0x2a2a2a, { bump: 1 })));
  const lens = mesh(new THREE.CylinderGeometry(0.022 * H, 0.025 * H, 0.04 * H, 12), metal(0x8a8a8a));
  lens.rotation.x = Math.PI / 2;
  lens.position.z = 0.045 * H;
  cam.add(lens);
  const flash = new THREE.Mesh(new THREE.SphereGeometry(0.012 * H, 8, 6), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  flash.position.set(0.03 * H, 0.04 * H, 0);
  cam.add(flash);
  g.add(cam);
  return { group: g, animate(t, w) { flash.visible = !w || Math.sin(t * 9) > 0.6; cam.rotation.y = Math.sin(t * 2) * 0.4 * w; } };
}

function bell(H) {
  const g = new THREE.Group();
  const spring = mesh(new THREE.TorusGeometry(0.012 * H, 0.004 * H, 4, 10), metal(0x9a9a90));
  spring.rotation.x = Math.PI / 2;
  spring.position.y = 0.04 * H;
  g.add(spring);
  g.add(stalk(H, 0.08 * H, 0.006 * H, metal(0x9a9a90)));
  const b = new THREE.Group();
  b.position.y = 0.1 * H;
  const dome = mesh(new THREE.SphereGeometry(0.04 * H, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), metal(0xd8a830, { rough: 0.25, rust: false }));
  dome.material.side = THREE.DoubleSide;
  b.add(dome);
  g.add(b);
  return { group: g, animate(t, w) { b.rotation.z = Math.sin(t * 30) * 0.35 * w; } };
}

function spareArm(H, { M }) {
  const g = new THREE.Group();
  g.add(stalk(H, 0.2 * H, 0.022 * H, M(0xc8a890)));
  return { group: g, animate(t, w) { g.rotation.z = Math.sin(t * 4) * 0.3 * w; } };
}
