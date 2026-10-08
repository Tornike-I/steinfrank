// Procedural stitched-together monsters. A monster is rebuilt identically from
// its seed, so only the seed needs to be saved. Appearance is independent of
// the prompt that produced it.
//
// Local space: feet at y = 0, facing +z.
//
// An optional `spec` (from a theme) biases the random choices — palette, body
// plan, limb/eye counts — and `spec.decorate` adds signature props via the
// returned anchors, so a weather monster looks like a weather monster while
// the seed still makes every individual unique.
import * as THREE from 'three';
import { Rng } from '../core/rng.js';
import { clay, glossy, metal, blood, glass, thread } from '../three/materials.js';
import { lumpy, sausage, mesh, stitches, ringSamples, ellipsoidLine } from '../three/geom.js';

const SKINS = [
  0x8fae5e, 0xb48aa8, 0xd8a48c, 0x86a0b6, 0xc8b464, 0xb05e50, 0x6f9488,
  0xe2cdb4, 0x9a7fc4, 0xd27d4a, 0x7cb8a0, 0xa8c27a, 0x5f7d9e, 0xc99a9a,
];
const IRIS = [0xf2d43a, 0xd8352a, 0x62d13d, 0x3aa4f2, 0xff8a1f, 0xb35cff, 0x111111, 0xf0f0e0];

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

// Point & normal on an ellipsoid from azimuth (around y, 0 = +z) and elevation.
function onEllipsoid(c, r, az, el, lift = 1) {
  const d = V(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  return {
    p: V(d.x * r.x, d.y * r.y, d.z * r.z).multiplyScalar(lift).add(c),
    n: V(d.x / r.x, d.y / r.y, d.z / r.z).normalize(),
  };
}

function orientTo(obj, normal) {
  obj.quaternion.setFromUnitVectors(V(0, 0, 1), normal.clone().normalize());
}

export function buildMonster(seed, spec = {}) {
  const r = new Rng(seed);
  const mats = [];
  const M = (color, o) => { const m = clay(color, o); mats.push(m); return m; };
  const pal = spec.palette || SKINS;
  const skin = r.pick(pal);
  const others = [...pal, ...(spec.palette ? SKINS.slice(0, 3) : [])].filter((c) => c !== skin);
  const W = (key, def) => spec[key] || def;
  const donor = () => (r.chance(0.5) ? skin : r.pick(others));
  const skinMat = M(skin);
  const threadMat = thread();
  const toothMat = clay(0xeee3c0, { rough: 0.5, bump: 0.8 });
  const darkMat = new THREE.MeshStandardMaterial({ color: 0x1a0606, roughness: 0.6 });
  const bloodMat = blood();
  mats.push(toothMat);

  const root = new THREE.Group();
  root.name = 'monster';
  const body = new THREE.Group();
  root.add(body);

  // --- torso ---------------------------------------------------------------
  const bodyType = r.weighted(W('bodies', [['blob', 3], ['tall', 2], ['pear', 2], ['bean', 1.4], ['stack', 1.4]]));
  let rad;
  switch (bodyType) {
    case 'blob': { const s = r.float(0.3, 0.42); rad = V(s, s * r.float(0.85, 1.05), s * r.float(0.8, 0.95)); break; }
    case 'tall': rad = V(r.float(0.2, 0.28), r.float(0.42, 0.55), r.float(0.18, 0.24)); break;
    case 'pear': rad = V(r.float(0.26, 0.34), r.float(0.34, 0.44), r.float(0.24, 0.3)); break;
    case 'bean': rad = V(r.float(0.42, 0.52), r.float(0.22, 0.28), r.float(0.26, 0.32)); break;
    default: rad = V(r.float(0.28, 0.36), r.float(0.26, 0.32), r.float(0.25, 0.3));
  }
  const legCount = r.weighted(W('legs', [[1, 0.6], [2, 5], [3, 1], [4, 2], [6, 0.8]]));
  const legLen = r.float(0.1, legCount >= 4 ? 0.28 : 0.42);
  body.position.y = legLen;
  const tc = V(0, rad.y * 0.88, 0);

  const torsoGeo = new THREE.SphereGeometry(1, 30, 22);
  torsoGeo.scale(rad.x, rad.y, rad.z);
  if (bodyType === 'pear') {
    const p = torsoGeo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const f = 1 + 0.28 * (-p.getY(i) / rad.y);
      p.setX(i, p.getX(i) * f); p.setZ(i, p.getZ(i) * f);
    }
  }
  if (r.chance(0.5)) { // pot belly
    const p = torsoGeo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const z = p.getZ(i);
      if (z > 0) p.setZ(i, z * (1 + 0.25 * Math.max(0, 1 - Math.abs(p.getY(i) / rad.y + 0.15) * 1.6)));
    }
  }
  lumpy(torsoGeo, 0.014, 4.5, seed % 997);
  const torso = mesh(torsoGeo, skinMat);
  torso.position.copy(tc);
  body.add(torso);

  // Where the top attachments (heads) go.
  let topC = tc.clone().add(V(0, rad.y * 0.92, 0));
  let topRad = rad;
  if (bodyType === 'stack') {
    const s = Math.min(rad.x, rad.z) * r.float(0.62, 0.8);
    const upper = V(s, s * 0.95, s * 0.95);
    const ug = new THREE.SphereGeometry(1, 24, 18);
    ug.scale(upper.x, upper.y, upper.z);
    lumpy(ug, 0.012, 5, seed % 991);
    const um = mesh(ug, M(donor()));
    const uc = tc.clone().add(V(0, rad.y * 0.8 + upper.y * 0.6, 0));
    um.position.copy(uc);
    body.add(um);
    body.add(stitches(ringSamples(tc.clone().add(V(0, rad.y * 0.8, 0)), V(0, 1, 0), Math.min(upper.x, rad.x) * 0.92, 14), { len: 0.05, mat: threadMat }));
    topC = uc.clone().add(V(0, upper.y * 0.9, 0));
    topRad = upper;
  }

  // Patches of donor skin, sewn on.
  const patchCount = r.int(1, 3);
  for (let i = 0; i < patchCount; i++) {
    const ang = r.float(0.35, 0.7);
    const g = new THREE.SphereGeometry(1, 16, 8, 0, Math.PI * 2, 0, ang);
    const dir = V(r.float(-1, 1), r.float(-0.6, 0.9), r.float(-0.2, 1)).normalize();
    g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), dir));
    g.scale(rad.x * 1.03, rad.y * 1.03, rad.z * 1.03);
    const pm = mesh(g, M(r.pick(others)));
    pm.position.copy(tc);
    body.add(pm);
    // Stitch around the patch edge.
    const ring = [];
    const u = V(1, 0, 0); if (Math.abs(dir.x) > 0.9) u.set(0, 0, 1);
    const v = V().crossVectors(dir, u).normalize(); u.crossVectors(v, dir).normalize();
    const n = 12;
    for (let k = 0; k < n; k++) {
      const a = (k / n) * Math.PI * 2;
      const d = dir.clone().multiplyScalar(Math.cos(ang)).addScaledVector(u, Math.sin(ang) * Math.cos(a)).addScaledVector(v, Math.sin(ang) * Math.sin(a));
      const s = onEllipsoid(V(), rad, Math.atan2(d.x, d.z), Math.asin(THREE.MathUtils.clamp(d.y, -1, 1)), 1.045);
      ring.push({ p: s.p.add(tc), n: s.n, t: null });
    }
    for (let k = 0; k < n; k++) ring[k].t = ring[(k + 1) % n].p.clone().sub(ring[(k + n - 1) % n].p).normalize();
    body.add(stitches(ring, { len: 0.04, mat: threadMat }));
  }

  // A long scar seam across the torso.
  if (r.chance(0.7)) {
    const a = V(r.float(-0.8, 0.8), r.float(-0.6, 0.8), 1), b = V(r.float(-0.8, 0.8), r.float(-0.6, 0.8), 1);
    body.add(stitches(ellipsoidLine(tc, rad, a, b, r.int(5, 9), 1.04), { len: 0.05, mat: threadMat }));
  }

  // Belly window with exposed organs.
  if (r.chance(0.28)) {
    const s = onEllipsoid(tc, rad, r.float(-0.3, 0.3), r.float(-0.3, 0.1), 0.97);
    const win = new THREE.Group();
    win.position.copy(s.p);
    orientTo(win, s.n);
    const hr = Math.min(rad.x, rad.y) * 0.38;
    const hole = mesh(new THREE.SphereGeometry(hr, 16, 10), darkMat);
    hole.scale.set(1, 0.85, 0.35);
    win.add(hole);
    const guts = mesh(new THREE.TorusKnotGeometry(hr * 0.42, hr * 0.17, 48, 8, 2, 3), M(0xd9707a, { rough: 0.3 }));
    guts.position.z = hr * 0.12;
    guts.scale.z = 0.6;
    win.add(guts);
    const rim = mesh(new THREE.TorusGeometry(hr, hr * 0.12, 8, 20), bloodMat);
    rim.scale.y = 0.85;
    win.add(rim);
    body.add(win);
    body.add(stitches(ringSamples(s.p, s.n, hr * 1.15, 12).map((q) => ({ ...q, n: s.n.clone(), t: q.t })), { len: 0.035, cross: false, mat: threadMat }));
  }

  // --- legs ------------------------------------------------------------------
  const legs = [];
  const hipPts = {
    1: [V(0, 0, 0)],
    2: [V(-0.5, 0, 0), V(0.5, 0, 0)],
    3: [V(-0.6, 0, 0.1), V(0, 0, -0.2), V(0.6, 0, 0.1)],
    4: [V(-0.55, 0, 0.45), V(0.55, 0, 0.45), V(-0.55, 0, -0.45), V(0.55, 0, -0.45)],
    6: [V(-0.65, 0, 0.5), V(0.65, 0, 0.5), V(-0.7, 0, 0), V(0.7, 0, 0), V(-0.65, 0, -0.5), V(0.65, 0, -0.5)],
  }[legCount];
  const legR = THREE.MathUtils.clamp(r.float(0.035, 0.085) * (legCount === 1 ? 1.6 : 1), 0.03, 0.13);
  const limp = r.chance(0.25) ? r.int(0, legCount - 1) : -1;
  hipPts.forEach((h, i) => {
    const pivot = new THREE.Group();
    pivot.position.set(h.x * rad.x, 0, h.z * rad.z);
    const len = legLen * (i === limp ? 0.75 : 1) + 0.04;
    const lr = legR * (i === limp ? 0.8 : 1);
    const legMat = r.chance(0.3) ? M(donor()) : skinMat;
    const lg = sausage(len, [lr, lr * 0.85, lr * 0.75, lr * 0.85]);
    lumpy(lg, 0.006, 9, i + seed % 101);
    pivot.add(mesh(lg, legMat));
    const footType = r.pick(['club', 'claw', 'hoof']);
    const foot = new THREE.Group();
    foot.position.y = -len + (i === limp ? 0 : 0) + lr * 0.4;
    if (footType === 'claw') {
      for (let k = -1; k <= 1; k++) {
        const toe = mesh(new THREE.ConeGeometry(lr * 0.35, lr * 1.6, 6), toothMat);
        toe.rotation.x = Math.PI / 2;
        toe.position.set(k * lr * 0.55, -lr * 0.25, lr * 1.0);
        foot.add(toe);
      }
      const pad = mesh(new THREE.SphereGeometry(lr * 1.05, 12, 8), legMat);
      pad.scale.set(1.1, 0.55, 1.3);
      foot.add(pad);
    } else if (footType === 'hoof') {
      const hoof = mesh(new THREE.CylinderGeometry(lr * 0.9, lr * 1.15, lr * 0.9, 10), darkMat);
      hoof.position.y = -lr * 0.1;
      foot.add(hoof);
    } else {
      const club = mesh(lumpy(new THREE.SphereGeometry(lr * 1.3, 14, 10), 0.01, 10, i), legMat);
      club.scale.set(1, 0.6, 1.5);
      club.position.z = lr * 0.5;
      foot.add(club);
    }
    pivot.add(foot);
    body.add(pivot);
    if (r.chance(0.35)) pivot.add(stitches(ringSamples(V(0, -len * 0.45, 0), V(0, 1, 0), lr * 0.95, 8), { len: 0.03, mat: threadMat }));
    legs.push({ pivot, phase: (i % 2) * Math.PI + (i >= 2 ? Math.PI / 2 : 0), len, kind: 'leg' });
  });

  // --- arms ------------------------------------------------------------------
  const arms = [];
  const armCount = Math.max(spec.minArms || 0, r.weighted(W('arms', [[0, 0.7], [1, 1], [2, 5], [3, 1], [4, 1.4]])));
  const bigArm = r.chance(0.3) ? r.int(0, Math.max(0, armCount - 1)) : -1;
  for (let i = 0; i < armCount; i++) {
    const side = armCount === 1 ? r.sign() : i % 2 === 0 ? -1 : 1;
    const row = Math.floor(i / 2);
    const el = (armCount === 3 && i === 2) ? r.float(-0.1, 0.2) : 0.35 - row * 0.45;
    const s = onEllipsoid(tc, rad, side * Math.PI / 2 * r.float(0.85, 1), el, 0.92);
    const pivot = new THREE.Group();
    pivot.position.copy(s.p);
    const scale = i === bigArm ? 1.55 : 1;
    const len = r.float(0.2, 0.5) * scale;
    const ar = r.float(0.028, 0.055) * scale;
    const type = i < 2 && spec.handArms ? 'mitten' : r.weighted(W('armTypes', [['mitten', 4], ['claw', 1.5], ['tentacle', 1.2], ['bone', 1]]));
    const armMat = r.chance(0.45) ? M(donor()) : skinMat;
    const restZ = side * r.float(0.25, 0.65);
    const arm = new THREE.Group();
    pivot.add(arm);
    if (type === 'bone') {
      const boneMat = M(0xe8dcc0, { rough: 0.6 });
      const upper = mesh(sausage(len * 0.4, [ar, ar * 0.8, ar]), armMat);
      arm.add(upper);
      const bone = mesh(new THREE.CylinderGeometry(ar * 0.35, ar * 0.35, len * 0.6, 8), boneMat);
      bone.position.y = -len * 0.4 - len * 0.3;
      arm.add(bone);
      const stump = mesh(new THREE.CylinderGeometry(ar * 0.95, ar * 0.95, ar * 0.25, 12), bloodMat);
      stump.position.y = -len * 0.4;
      arm.add(stump);
      const knuckle = mesh(new THREE.SphereGeometry(ar * 0.55, 10, 8), boneMat);
      knuckle.position.y = -len;
      arm.add(knuckle);
      arm.userData.end = new THREE.Group();
      arm.userData.end.position.y = -len - ar;
      arm.add(arm.userData.end);
      for (let k = -1; k <= 1; k++) {
        const f = mesh(new THREE.CylinderGeometry(ar * 0.12, ar * 0.1, ar * 1.8, 6), boneMat);
        f.position.set(k * ar * 0.35, -len - ar * 0.9, 0);
        f.rotation.z = k * 0.25;
        arm.add(f);
      }
    } else if (type === 'tentacle') {
      let parent = arm, segLen = len * 0.38, rr = ar * 1.2;
      const segs = [];
      for (let k = 0; k < 4; k++) {
        const seg = new THREE.Group();
        seg.position.y = k === 0 ? 0 : -segLen * 0.92;
        seg.add(mesh(sausage(segLen, [rr, rr * 0.8]), armMat));
        parent.add(seg);
        segs.push(seg);
        parent = seg;
        rr *= 0.72;
      }
      arm.userData.segs = segs;
      arm.userData.end = new THREE.Group();
      arm.userData.end.position.y = -segLen;
      parent.add(arm.userData.end);
    } else {
      arm.add(mesh(lumpy(sausage(len, [ar, ar * 0.75, ar * 0.9, ar * 0.8]), 0.005, 9, i), armMat));
      const hand = new THREE.Group();
      hand.position.y = -len;
      arm.add(hand);
      arm.userData.end = new THREE.Group();
      arm.userData.end.position.y = -ar * 1.4;
      hand.add(arm.userData.end);
      if (type === 'claw') {
        const clawMat = M(r.pick([0xb04a3a, 0x6b4a8a, 0xc9a24a, donor()]), { rough: 0.45 });
        for (const k of [-1, 1]) {
          const pin = mesh(new THREE.ConeGeometry(ar * 1.0, ar * 4.2, 8), clawMat);
          pin.position.set(k * ar * 0.75, -ar * 1.9, 0);
          pin.rotation.set(Math.PI, 0, -k * 0.3);
          hand.add(pin);
        }
        hand.add(mesh(new THREE.SphereGeometry(ar * 1.5, 12, 10), clawMat));
      } else {
        const palm = mesh(lumpy(new THREE.SphereGeometry(ar * 1.6, 12, 10), 0.006, 12, i), armMat);
        palm.scale.set(1, 1.1, 0.7);
        palm.position.y = -ar * 1.1;
        hand.add(palm);
        const fingers = r.int(2, 4);
        for (let k = 0; k < fingers; k++) {
          const f = mesh(sausage(ar * 2.1, [ar * 0.45, ar * 0.4, ar * 0.35]), armMat);
          f.position.set((k - (fingers - 1) / 2) * ar * 0.85, -ar * 2.1, 0);
          f.rotation.set(0.25, 0, (k - (fingers - 1) / 2) * 0.2);
          hand.add(f);
        }
      }
    }
    pivot.rotation.z = restZ;
    body.add(pivot);
    if (r.chance(0.6)) body.add(stitches(ringSamples(s.p, s.n, ar * 1.25, 9), { len: 0.03, mat: threadMat }));
    if (r.chance(0.18)) {
      const band = mesh(new THREE.TorusGeometry(ar * 1.15, ar * 0.45, 6, 14), clay(0xd9d0b8, { rough: 1 }));
      band.rotation.x = Math.PI / 2;
      band.position.y = -len * 0.5;
      arm.add(band);
    }
    arms.push({ pivot, arm, restZ, side, phase: r.float(0, 6.28), len, type, kind: 'arm' });
  }

  // --- heads -----------------------------------------------------------------
  const heads = [];
  const eyes = [];
  const mouths = [];
  const headCount = r.weighted(W('heads', [[0, 1.3], [1, 5], [2, 1.1]]));
  const faces = [];
  if (headCount === 0) {
    faces.push({ parent: torso, c: V(), r: rad, isTorso: true });
  }
  for (let h = 0; h < headCount; h++) {
    const hr = r.float(0.12, 0.26) * (headCount === 2 ? 0.8 : 1);
    const hrad = V(hr * r.float(0.9, 1.3), hr * r.float(0.85, 1.25), hr * r.float(0.85, 1.05));
    const pivot = new THREE.Group();
    const xoff = headCount === 2 ? (h === 0 ? -1 : 1) * topRad.x * 0.5 : r.float(-0.04, 0.04);
    pivot.position.copy(topC).add(V(xoff, -0.02, topRad.z * 0.1));
    pivot.rotation.z = headCount === 2 ? (h === 0 ? 0.25 : -0.25) : 0;
    const neckLen = r.chance(0.4) ? r.float(0.05, 0.16) : 0.0;
    if (neckLen > 0) {
      const neck = mesh(sausage(neckLen + hr * 0.5, [hr * 0.35, hr * 0.3]), skinMat);
      neck.rotation.x = Math.PI; // grow upward
      pivot.add(neck);
    }
    const hg = new THREE.SphereGeometry(1, 26, 20);
    hg.scale(hrad.x, hrad.y, hrad.z);
    const shape = r.pick(['egg', 'egg', 'cone', 'flat', 'jaw']);
    const p = hg.attributes.position;
    for (let k = 0; k < p.count; k++) {
      const y = p.getY(k) / hrad.y;
      let f = 1;
      if (shape === 'cone') f = 1 - Math.max(0, y) * 0.45;
      if (shape === 'egg') f = 1 + 0.15 * y;
      if (shape === 'jaw') f = 1 + Math.max(0, -y) * 0.35;
      p.setX(k, p.getX(k) * f);
      p.setZ(k, p.getZ(k) * (shape === 'flat' ? 0.85 : f));
    }
    lumpy(hg, 0.01, 6, seed % 89 + h);
    const head = new THREE.Group();
    head.position.y = neckLen + hrad.y * 0.85;
    pivot.add(head);
    const headMat = r.chance(0.35) ? M(donor()) : skinMat;
    const hm = mesh(hg, headMat);
    head.add(hm);
    body.add(pivot);
    const topA = new THREE.Group();
    topA.position.y = hrad.y * 0.9;
    head.add(topA);
    const fa = onEllipsoid(V(), hrad, 0, 0.2, 1.0);
    const faceA = new THREE.Group();
    faceA.position.copy(fa.p);
    orientTo(faceA, fa.n);
    head.add(faceA);
    heads.push({ pivot, head, phase: r.float(0, 6.28), kind: 'head', mat: headMat, top: topA, face: faceA, rad: hrad });
    faces.push({ parent: head, c: V(), r: hrad, isTorso: false });

    // Neck stitches and bolts.
    if (r.chance(0.6)) pivot.add(stitches(ringSamples(V(0, neckLen + 0.01, 0), V(0, 1, 0), hr * 0.42, 10), { len: 0.035, mat: threadMat }));
    if (r.chance(0.4)) {
      for (const sx of [-1, 1]) {
        const bolt = mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.12, 8), metal(0x8a8a80));
        bolt.rotation.z = Math.PI / 2;
        bolt.position.set(sx * hrad.x * 0.85, -hrad.y * 0.35, 0);
        const cap = mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.025, 6), metal(0x6f6a60));
        cap.rotation.z = Math.PI / 2;
        cap.position.set(sx * 0.06, 0, 0);
        bolt.add(cap);
        head.add(bolt);
      }
    }
    // Top of the head.
    const top = r.weighted(W('tops', [['none', 3], ['horns', 2], ['brain', 1], ['jar', 0.8], ['tuft', 1.2], ['antennae', 1]]));
    if (top === 'horns') {
      const hornMat = M(r.pick([0xe8dcc0, 0x3a2a20, 0x9a3a2a]), { rough: 0.5 });
      const mism = r.chance(0.4);
      for (const sx of [-1, 1]) {
        const hl = r.float(0.08, 0.2) * (mism && sx > 0 ? 0.5 : 1);
        const horn = mesh(new THREE.ConeGeometry(hr * 0.18, hl, 8), hornMat);
        horn.position.set(sx * hrad.x * 0.55, hrad.y * 0.75 + hl * 0.4, 0);
        horn.rotation.z = -sx * r.float(0.2, 0.7);
        head.add(horn);
      }
    } else if (top === 'brain' || top === 'jar') {
      const bg = lumpy(new THREE.SphereGeometry(hrad.x * 0.62, 20, 14), 0.02, 18, seed % 13);
      const brain = mesh(bg, M(0xe48a9a, { rough: 0.35, bump: 4 }));
      brain.scale.set(1, 0.7, 1.05);
      brain.position.y = hrad.y * 0.72;
      head.add(brain);
      if (top === 'jar') {
        const dome = new THREE.Mesh(new THREE.SphereGeometry(hrad.x * 0.8, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), glass(0xcfe8d0, 0.28));
        dome.position.y = hrad.y * 0.55;
        head.add(dome);
        const ringM = mesh(new THREE.TorusGeometry(hrad.x * 0.8, 0.012, 6, 24), metal(0xb08a4a));
        ringM.rotation.x = Math.PI / 2;
        ringM.position.y = hrad.y * 0.55;
        head.add(ringM);
      } else {
        head.add(stitches(ringSamples(V(0, hrad.y * 0.62, 0), V(0, 1, 0), hrad.x * 0.66, 12), { len: 0.035, mat: threadMat }));
      }
    } else if (top === 'tuft') {
      const hairMat = M(r.pick([0x1d1a16, 0xe8e4d8, 0x8a2a1a, 0x3a5a2a]), { rough: 1 });
      for (let k = 0; k < 7; k++) {
        const strand = mesh(new THREE.ConeGeometry(0.012, r.float(0.06, 0.16), 5), hairMat);
        strand.position.set(r.float(-0.4, 0.4) * hrad.x, hrad.y * 0.92, r.float(-0.3, 0.2) * hrad.z);
        strand.rotation.set(r.float(-0.5, 0.5), 0, r.float(-0.7, 0.7));
        head.add(strand);
      }
    } else if (top === 'antennae') {
      for (const sx of [-1, 1]) {
        const stalk = mesh(new THREE.CylinderGeometry(0.007, 0.01, 0.16, 5), headMat);
        stalk.position.set(sx * hrad.x * 0.35, hrad.y + 0.06, 0);
        stalk.rotation.z = -sx * 0.4;
        head.add(stalk);
        const bulb = mesh(new THREE.SphereGeometry(0.025, 10, 8), glossy(r.pick([0xd8f03a, 0xff4a3a, 0x7af0ff]), { emissive: 0x442200, ei: 0.4 }));
        bulb.position.set(sx * hrad.x * 0.35 + sx * 0.06, hrad.y + 0.13, 0);
        head.add(bulb);
      }
    }
    // Ears.
    if (r.chance(0.35)) {
      for (const sx of [-1, 1]) {
        const es = r.float(0.05, 0.12) * (r.chance(0.3) ? 0.5 : 1);
        const ear = mesh(lumpy(new THREE.SphereGeometry(es, 12, 8), 0.004, 20, sx), r.chance(0.3) ? M(donor()) : headMat);
        ear.scale.set(0.3, 1, 0.8);
        ear.position.set(sx * (hrad.x + es * 0.1), hrad.y * 0.2, -0.01);
        ear.rotation.z = -sx * 0.5;
        head.add(ear);
      }
    }
    if (r.chance(0.22)) {
      const band = mesh(new THREE.TorusGeometry(hrad.x * 1.0, 0.025, 6, 22), clay(0xdcd2bc, { rough: 1 }));
      band.rotation.x = Math.PI / 2 + r.float(-0.3, 0.3);
      band.position.y = hrad.y * 0.3;
      head.add(band);
    }
  }

  // --- faces: eyes and mouths ----------------------------------------------------
  for (const face of faces) {
    const n = r.weighted(W('eyes', [[1, 2.5], [2, 3.5], [3, 2], [4, 1], [5, 0.6], [6, 0.4]]));
    const fr = face.r;
    const baseSize = Math.min(fr.x, fr.y) * (n === 1 ? r.float(0.4, 0.55) : r.float(0.2, 0.33) * (n > 3 ? 0.75 : 1));
    const placed = [];
    for (let k = 0; k < n; k++) {
      let az = 0, el = 0, es = baseSize * r.float(0.65, 1.35);
      for (let tries = 0; tries < 20; tries++) {
        az = n === 1 ? r.float(-0.1, 0.1) : r.float(-0.75, 0.75);
        el = n === 1 ? r.float(0.05, 0.3) : r.float(-0.05, face.isTorso ? 0.45 : 0.6);
        if (n === 2 && k === 1 && placed[0]) { az = -placed[0].az + r.float(-0.12, 0.12); el = placed[0].el + r.float(-0.15, 0.15); }
        const ok = placed.every((q) => Math.hypot(q.az - az, q.el - el) > (q.es + es) / Math.min(fr.x, fr.y) * 0.95);
        if (ok) break;
      }
      placed.push({ az, el, es });
      const s = onEllipsoid(face.c, fr, az, el, 0.93);
      const eye = buildEye(r, es, face.isTorso ? skinMat : (heads[faces.indexOf(face) - (headCount === 0 ? 0 : 0)]?.mat || skinMat));
      eye.group.position.copy(s.p);
      orientTo(eye.group, s.n);
      face.parent.add(eye.group);
      eyes.push(eye);
    }
    // Mouth
    const mType = r.weighted([['grin', 3], ['fangs', 2], ['stitched', 1.2], ['o', 1]]);
    const ms = onEllipsoid(face.c, fr, r.float(-0.08, 0.08), face.isTorso ? -0.25 : -0.35, 0.96);
    const mouth = new THREE.Group();
    mouth.position.copy(ms.p);
    orientTo(mouth, ms.n);
    const mw = fr.x * r.float(0.35, 0.75), mh = fr.y * (mType === 'o' ? 0.22 : r.float(0.07, 0.16));
    const cavity = mesh(new THREE.SphereGeometry(1, 16, 10), darkMat, { cast: false });
    cavity.scale.set(mw, mh, Math.min(mw, mh) * 0.5 + 0.01);
    mouth.add(cavity);
    if (mType === 'stitched') {
      const pts = [];
      for (let k = 0; k < 6; k++) pts.push({ p: V((k / 5 - 0.5) * mw * 1.6, 0, mh * 0.4), n: V(0, 0, 1), t: V(1, 0, 0) });
      cavity.scale.y *= 0.35;
      mouth.add(stitches(pts, { len: mh * 3 + 0.03, cross: false, mat: threadMat }));
    } else {
      const tn = mType === 'fangs' ? 2 : r.int(3, 8);
      for (let k = 0; k < tn; k++) {
        if (mType !== 'fangs' && r.chance(0.2)) continue; // missing tooth
        const tw = mType === 'fangs' ? mw * 0.18 : (mw * 1.6) / tn * 0.42;
        const th = mType === 'fangs' ? mh * 2.4 : mh * r.float(0.7, 1.3);
        const tooth = mesh(r.chance(0.5) ? new THREE.ConeGeometry(tw, th, 5) : new THREE.BoxGeometry(tw * 1.6, th, tw), toothMat, { cast: false });
        const x = mType === 'fangs' ? (k === 0 ? -0.5 : 0.5) * mw : (k / Math.max(1, tn - 1) - 0.5) * mw * 1.5;
        tooth.position.set(x, mh * 0.55 - th * 0.35, mh * 0.35);
        tooth.rotation.x = Math.PI;
        tooth.rotation.z = r.float(-0.25, 0.25);
        mouth.add(tooth);
      }
    }
    face.parent.add(mouth);
    mouths.push({ group: mouth, cavity, baseY: cavity.scale.y, type: mType });
  }

  // --- back extras -------------------------------------------------------------
  let tail = null;
  if (r.chance(0.35)) {
    tail = new THREE.Group();
    const s = onEllipsoid(tc, rad, Math.PI, -0.35, 0.9);
    tail.position.copy(s.p);
    let parent = tail, tl = r.float(0.12, 0.22), tr = r.float(0.03, 0.06);
    const tm = r.chance(0.5) ? skinMat : M(donor());
    const segs = [];
    for (let k = 0; k < 4; k++) {
      const seg = new THREE.Group();
      seg.position.y = k === 0 ? 0 : -tl * 0.9;
      seg.rotation.x = k === 0 ? 2.0 : 0.35;
      seg.add(mesh(sausage(tl, [tr, tr * 0.75]), tm));
      parent.add(seg);
      segs.push(seg);
      parent = seg;
      tr *= 0.75;
    }
    if (r.chance(0.5)) {
      const spike = mesh(new THREE.ConeGeometry(tr * 2.2, tl * 0.8, 6), toothMat);
      spike.position.y = -tl;
      spike.rotation.x = Math.PI;
      parent.add(spike);
    }
    tail.userData.segs = segs;
    body.add(tail);
  }
  if (r.chance(0.28)) {
    const spikeMat = M(r.pick([0x3a2a20, 0xe8dcc0, 0x7a2a4a]), { rough: 0.5 });
    const n = r.int(3, 6);
    for (let k = 0; k < n; k++) {
      const el = 0.7 - (k / (n - 1 || 1)) * 1.1;
      const s = onEllipsoid(tc, rad, Math.PI, el, 0.95);
      const sp = mesh(new THREE.ConeGeometry(0.03, r.float(0.06, 0.12), 6), spikeMat);
      sp.position.copy(s.p);
      sp.quaternion.setFromUnitVectors(V(0, 1, 0), s.n);
      body.add(sp);
    }
  }
  if (headCount === 0 && r.chance(0.5)) {
    for (const sx of [-1, 1]) {
      const bolt = mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.14, 8), metal(0x8a8a80));
      const s = onEllipsoid(tc, rad, sx * Math.PI / 2, 0.55, 0.95);
      bolt.position.copy(s.p);
      bolt.quaternion.setFromUnitVectors(V(0, 1, 0), s.n);
      body.add(bolt);
    }
  }

  // --- normalise size -------------------------------------------------------------
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const h = box.max.y - box.min.y;
  const target = r.float(0.75, 1.15);
  // Fit within a height but also a width/depth budget so squat blobs stay table-sized.
  const k = Math.min(target / h, 0.8 / (box.max.x - box.min.x), 0.62 / (box.max.z - box.min.z));
  root.scale.setScalar(k);
  root.updateMatrixWorld(true);
  box.setFromObject(root);

  // Anchors for themed props.
  const torsoTop = new THREE.Group();
  torsoTop.position.copy(topC);
  body.add(torsoTop);
  const ch = onEllipsoid(tc, rad, 0, 0.15, 1.0);
  const chest = new THREE.Group();
  chest.position.copy(ch.p);
  orientTo(chest, ch.n);
  body.add(chest);
  const tf = onEllipsoid(tc, rad, 0, 0.35, 1.0);
  const torsoFace = new THREE.Group();
  torsoFace.position.copy(tf.p);
  orientTo(torsoFace, tf.n);
  body.add(torsoFace);
  const handOf = (side) => arms.find((a) => a.side === side)?.arm.userData.end || null;
  const anchors = {
    top: heads[0]?.top || torsoTop,
    face: heads[0]?.face || torsoFace,
    faceRad: heads[0]?.rad || rad,
    handR: handOf(-1), handL: handOf(1),
    armR: arms.find((a) => a.side === -1) || null, armL: arms.find((a) => a.side === 1) || null,
    chest, torsoRadii: rad,
  };

  const gait = legCount === 1 ? 'hop' : legCount === 2 ? 'waddle' : 'scuttle';
  const monster = {
    seed, root, body, torso, torsoCenter: tc, torsoRadii: rad, legs, arms, heads, eyes, mouths, tail,
    mats, gait, scale: k, bodyLift: legLen,
    height: box.max.y - box.min.y,
    width: box.max.x - box.min.x,
    back: -box.min.z,
    front: box.max.z,
    speed: r.float(0.6, 1.4) * (gait === 'scuttle' ? 1.3 : 1),
    blinkRate: r.float(2, 6),
    personality: r.float(0, 1),
    anchors, updaters: [], theme: spec.id || null,
  };
  if (spec.decorate) {
    spec.decorate({ m: monster, r, anchors, M });
    root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    root.updateMatrixWorld(true);
  }
  return monster;
}

function buildEye(r, es, lidMat) {
  const group = new THREE.Group();
  const white = mesh(new THREE.SphereGeometry(es, 16, 12), glossy(r.chance(0.2) ? 0xe8d870 : 0xf4f0e2), { cast: false });
  group.add(white);
  const look = new THREE.Group();
  group.add(look);
  const irisR = es * r.float(0.42, 0.7);
  const iris = mesh(new THREE.SphereGeometry(irisR, 14, 10), glossy(r.pick(IRIS)), { cast: false });
  iris.scale.z = 0.35;
  iris.position.z = es * 0.9;
  look.add(iris);
  const pupil = mesh(new THREE.SphereGeometry(irisR * 0.55, 12, 8), glossy(0x050505), { cast: false });
  pupil.scale.set(r.chance(0.25) ? 0.35 : 1, 1, 0.35);
  pupil.position.z = es * 0.98;
  look.add(pupil);
  const hl = new THREE.Mesh(new THREE.SphereGeometry(irisR * 0.18, 8, 6), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  hl.position.set(irisR * 0.3, irisR * 0.35, es * 1.0);
  look.add(hl);
  // Eyelid: a hemisphere that rotates down to blink.
  const lid = mesh(new THREE.SphereGeometry(es * 1.08, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), lidMat, { cast: false });
  const open = r.float(-0.25, 0.45);
  lid.rotation.x = open;
  group.add(lid);
  // Dead "X" eyes.
  const x = new THREE.Group();
  const xm = new THREE.MeshBasicMaterial({ color: 0x120808 });
  for (const a of [0.78, -0.78]) {
    const bar = new THREE.Mesh(new THREE.BoxGeometry(es * 1.5, es * 0.22, es * 0.1), xm);
    bar.rotation.z = a;
    x.add(bar);
  }
  x.position.z = es * 1.02;
  x.visible = false;
  group.add(x);
  return { group, look, lid, open, x, size: es };
}

// Desaturate (inert on the slab, or dead) — f in 0..1.
const GREY = new THREE.Color(0x7d8378);
export function setPallor(monster, f, tint = GREY) {
  for (const m of monster.mats) {
    if (!m.userData.baseColor) continue;
    m.color.copy(m.userData.baseColor).lerp(tint, f);
  }
}

export function setDeadEyes(monster, dead) {
  for (const e of monster.eyes) {
    e.x.visible = dead;
    e.look.visible = !dead;
    e.lid.rotation.x = dead ? 0.2 : e.open;
  }
}

export function disposeMonster(monster) {
  monster.root.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
  });
  for (const m of monster.mats) m.dispose();
}
