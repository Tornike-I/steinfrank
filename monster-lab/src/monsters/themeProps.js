// Signature props for themed monsters. Sizes are in the monster's own
// (pre-normalisation) units; `H` is the monster's unscaled height.
import * as THREE from 'three';
import { clay, metal, glass, glossy, cloth, rubber } from '../three/materials.js';
import { lumpy, sausage, mesh } from '../three/geom.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

// Attach so the prop's +Y points up in monster space at the rest pose.
export function attachUpright(anchor, obj, up = V(0, 1, 0)) {
  anchor.add(obj);
  anchor.updateWorldMatrix(true, false);
  const q = new THREE.Quaternion();
  anchor.getWorldQuaternion(q);
  obj.quaternion.copy(q.invert()).multiply(new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), up.clone().normalize()));
  return obj;
}

// Prefer the right hand, then the left; otherwise float it beside the body.
export function holdProp(api, obj, { prefer = 'R', float = V(0.45, 0.6, 0.2) } = {}) {
  const a = api.anchors;
  const hand = prefer === 'R' ? (a.handR || a.handL) : (a.handL || a.handR);
  if (hand) return { obj: attachUpright(hand, obj), hand, floating: false };
  const H = api.m.height / api.m.scale;
  obj.position.copy(float).multiplyScalar(H);
  api.m.body.add(obj);
  api.m.updaters.push((t) => { obj.position.y = float.y * H + Math.sin(t * 2.2) * 0.03 * H; obj.rotation.y = Math.sin(t * 0.7) * 0.4; });
  return { obj, hand: null, floating: true };
}

// --------------------------------------------------------------- weather --
export function stormCloud(api, size) {
  const g = new THREE.Group();
  const mat = api.M(0x9aa4b0, { rough: 1, bump: 3 });
  const puffs = [[0, 0, 0, 1], [-0.7, -0.15, 0.1, 0.75], [0.7, -0.1, 0, 0.8], [0.25, 0.35, -0.1, 0.7], [-0.3, 0.3, 0.15, 0.6]];
  for (const [x, y, z, s] of puffs) {
    const p = mesh(lumpy(new THREE.SphereGeometry(size * s, 14, 10), size * 0.06, 6 / size, x * 10), mat);
    p.position.set(x * size, y * size + size * 0.9, z * size);
    g.add(p);
  }
  const dropMat = glossy(0x6ab0ff, { emissive: 0x1a4aaa, ei: 0.5 });
  const drops = [];
  for (let i = 0; i < 9; i++) {
    const d = mesh(new THREE.ConeGeometry(size * 0.06, size * 0.22, 6), dropMat, { cast: false });
    d.rotation.x = Math.PI;
    d.userData.x = (Math.random() - 0.5) * size * 1.6;
    d.userData.z = (Math.random() - 0.5) * size * 0.8;
    d.userData.o = Math.random();
    g.add(d);
    drops.push(d);
  }
  const bolt = new THREE.Mesh(new THREE.ConeGeometry(size * 0.12, size * 0.6, 3), new THREE.MeshBasicMaterial({ color: 0xfff27a }));
  bolt.position.set(size * 0.2, size * 0.25, size * 0.3);
  bolt.rotation.z = 0.4;
  bolt.visible = false;
  g.add(bolt);
  api.m.updaters.push((t, s) => {
    const rain = 0.6 + (s.intensity || 0) * 2.5;
    for (const d of drops) {
      const u = (t * rain * 0.9 + d.userData.o) % 1;
      d.position.set(d.userData.x, size * 0.55 - u * size * 1.9, d.userData.z);
      d.visible = u < 0.9;
    }
    bolt.visible = (s.intensity || 0) > 0.5 && Math.sin(t * 23) > 0.85;
    g.position.y = Math.sin(t * 1.3) * size * 0.08;
  });
  return g;
}

export function umbrella(H) {
  const g = new THREE.Group();
  const shaft = mesh(new THREE.CylinderGeometry(0.008 * H, 0.008 * H, 0.55 * H, 6), metal(0x2a2a2a));
  shaft.position.y = 0.2 * H;
  g.add(shaft);
  const hook = mesh(new THREE.TorusGeometry(0.035 * H, 0.008 * H, 6, 10, Math.PI), clay(0x5a2a1a));
  hook.position.y = -0.07 * H;
  hook.rotation.z = Math.PI;
  g.add(hook);
  const canopy = mesh(new THREE.ConeGeometry(0.32 * H, 0.16 * H, 8, 1, true), cloth(0xb02a2a));
  canopy.material.side = THREE.DoubleSide;
  canopy.position.y = 0.52 * H;
  g.add(canopy);
  // Torn panel: a patched different-colour wedge.
  const patch = mesh(new THREE.ConeGeometry(0.325 * H, 0.165 * H, 8, 1, true, 0, Math.PI / 4), cloth(0x2a4a8a));
  patch.material.side = THREE.DoubleSide;
  patch.position.y = 0.52 * H;
  g.add(patch);
  return g;
}

// --------------------------------------------------------------- dossier --
export function glasses(rad) {
  const g = new THREE.Group();
  const r = Math.min(rad.x, rad.y) * 0.28;
  const frame = metal(0x2a2018, { rust: false });
  for (const sx of [-1, 1]) {
    const ring = mesh(new THREE.TorusGeometry(r, r * 0.12, 6, 18), frame);
    ring.position.set(sx * r * 1.15, 0, r * 0.4);
    g.add(ring);
    const lens = new THREE.Mesh(new THREE.CircleGeometry(r, 16), glass(0xdfefff, 0.25));
    lens.position.set(sx * r * 1.15, 0, r * 0.42);
    g.add(lens);
  }
  const bridge = mesh(new THREE.CylinderGeometry(r * 0.08, r * 0.08, r * 0.4, 5), frame);
  bridge.rotation.z = Math.PI / 2;
  bridge.position.z = r * 0.45;
  g.add(bridge);
  return g;
}

export function tie(api, rad) {
  const g = new THREE.Group();
  const mat = api.M(0x8a1a2a, { rough: 0.7, bump: 1 });
  const knot = mesh(new THREE.SphereGeometry(rad.x * 0.1, 8, 6), mat);
  g.add(knot);
  const blade = mesh(new THREE.ConeGeometry(rad.x * 0.16, rad.y * 0.7, 4), mat);
  blade.rotation.z = Math.PI;
  blade.scale.z = 0.25;
  blade.position.y = -rad.y * 0.38;
  g.add(blade);
  return g;
}

export function folder(H) {
  const g = new THREE.Group();
  const mat = clay(0xd8b878, { rough: 0.9, bump: 1 });
  const back = mesh(new THREE.BoxGeometry(0.26 * H, 0.32 * H, 0.012 * H), mat);
  back.position.y = 0.12 * H;
  g.add(back);
  for (let i = 0; i < 3; i++) {
    const paper = mesh(new THREE.BoxGeometry(0.22 * H, 0.28 * H, 0.004 * H), clay(0xf2eedc, { rough: 1, bump: 0.5 }));
    paper.position.set((i - 1) * 0.01 * H, 0.14 * H + i * 0.012 * H, 0.012 * H + i * 0.004 * H);
    paper.rotation.z = (i - 1) * 0.06;
    g.add(paper);
  }
  const stamp = mesh(new THREE.BoxGeometry(0.1 * H, 0.04 * H, 0.002 * H), new THREE.MeshBasicMaterial({ color: 0xb02020 }));
  stamp.position.set(0, 0.22 * H, 0.03 * H);
  stamp.rotation.z = 0.2;
  g.add(stamp);
  return g;
}

export function pencil(H) {
  const g = new THREE.Group();
  const body = mesh(new THREE.CylinderGeometry(0.012 * H, 0.012 * H, 0.2 * H, 6), clay(0xf0c030, { bump: 0.5 }));
  g.add(body);
  const tip = mesh(new THREE.ConeGeometry(0.012 * H, 0.04 * H, 6), clay(0xe8c8a0));
  tip.position.y = -0.12 * H;
  tip.rotation.x = Math.PI;
  g.add(tip);
  const eraser = mesh(new THREE.CylinderGeometry(0.013 * H, 0.013 * H, 0.025 * H, 6), clay(0xe07a8a));
  eraser.position.y = 0.11 * H;
  g.add(eraser);
  return g;
}

// -------------------------------------------------------------- research --
export function magnifier(H) {
  const g = new THREE.Group();
  const handle = mesh(new THREE.CylinderGeometry(0.018 * H, 0.022 * H, 0.18 * H, 8), clay(0x4a2a18));
  g.add(handle);
  const ring = mesh(new THREE.TorusGeometry(0.11 * H, 0.016 * H, 8, 22), metal(0xb8893a, { rough: 0.3 }));
  ring.position.y = 0.21 * H;
  g.add(ring);
  const lens = new THREE.Mesh(new THREE.CircleGeometry(0.105 * H, 22), glass(0xe8f8ff, 0.3));
  lens.material.side = THREE.DoubleSide;
  lens.position.y = 0.21 * H;
  g.add(lens);
  return g;
}

export function bookStack(api, size) {
  const g = new THREE.Group();
  const cols = [0x7a2a2a, 0x2a4a6a, 0x4a6a2a, 0x6a4a2a];
  let y = 0;
  for (let i = 0; i < 3; i++) {
    const h = size * (0.22 + (i % 2) * 0.08);
    const b = mesh(new THREE.BoxGeometry(size * (1.1 - i * 0.15), h, size * 0.8), api.M(cols[i % cols.length], { rough: 0.8, bump: 1 }));
    b.position.y = y + h / 2;
    b.rotation.y = (i - 1) * 0.35;
    g.add(b);
    const pages = mesh(new THREE.BoxGeometry(size * (1.06 - i * 0.15), h * 0.7, size * 0.82), clay(0xf0e8d0, { bump: 0.4 }));
    pages.position.copy(b.position);
    pages.rotation.copy(b.rotation);
    pages.position.x += size * 0.03;
    g.add(pages);
    y += h;
  }
  return g;
}

export function headlamp(rad) {
  const g = new THREE.Group();
  const strap = mesh(new THREE.TorusGeometry(rad.x * 1.02, rad.x * 0.06, 6, 24), clay(0x2a2a2a));
  strap.rotation.x = Math.PI / 2;
  g.add(strap);
  const lamp = mesh(new THREE.CylinderGeometry(rad.x * 0.16, rad.x * 0.2, rad.x * 0.18, 10), metal(0x8a8a80));
  lamp.rotation.x = Math.PI / 2;
  lamp.position.z = rad.z * 1.02;
  g.add(lamp);
  const bulb = new THREE.Mesh(new THREE.CircleGeometry(rad.x * 0.14, 12), new THREE.MeshBasicMaterial({ color: 0xfff2b0 }));
  bulb.position.z = rad.z * 1.12;
  g.add(bulb);
  return g;
}

// ---------------------------------------------------------------- writer --
export function quill(H) {
  const g = new THREE.Group();
  const shaft = mesh(sausage(0.3 * H, [0.006 * H, 0.008 * H, 0.004 * H]), clay(0xe8e0d0));
  shaft.rotation.x = Math.PI;
  shaft.position.y = -0.06 * H;
  g.add(shaft);
  const vane = mesh(lumpy(new THREE.SphereGeometry(0.05 * H, 10, 8), 0.004 * H, 40, 3), clay(0x5a2a6a, { rough: 0.9, bump: 3 }));
  vane.scale.set(0.35, 2.4, 1);
  vane.position.y = 0.14 * H;
  g.add(vane);
  const nib = mesh(new THREE.ConeGeometry(0.008 * H, 0.04 * H, 5), metal(0x222222, { rust: false }));
  nib.rotation.x = Math.PI;
  nib.position.y = -0.08 * H;
  g.add(nib);
  return g;
}

export function beret(api, rad) {
  const g = new THREE.Group();
  const mat = api.M(0x1a1a2a, { rough: 1, bump: 2 });
  const cap = mesh(new THREE.SphereGeometry(rad.x * 0.85, 16, 8), mat);
  cap.scale.set(1.1, 0.32, 1.05);
  g.add(cap);
  const stub = mesh(new THREE.CylinderGeometry(rad.x * 0.04, rad.x * 0.06, rad.x * 0.16, 6), mat);
  stub.position.y = rad.x * 0.3;
  g.add(stub);
  g.rotation.z = 0.35;
  g.position.x = -rad.x * 0.15;
  return g;
}

// --------------------------------------------------------------- numbers --
export function abacus(H) {
  const g = new THREE.Group();
  const wood = clay(0x6a4020, { bump: 1.5 });
  const w = 0.28 * H, h = 0.2 * H;
  for (const y of [0, h]) {
    const bar = mesh(new THREE.BoxGeometry(w, 0.02 * H, 0.03 * H), wood);
    bar.position.y = y;
    g.add(bar);
  }
  for (const x of [-w / 2, w / 2]) {
    const side = mesh(new THREE.BoxGeometry(0.02 * H, h, 0.03 * H), wood);
    side.position.set(x, h / 2, 0);
    g.add(side);
  }
  const beadCols = [0xd83a2a, 0xf0c030, 0x3a8ad8, 0x4ab84a];
  const beads = [];
  for (let row = 0; row < 4; row++) {
    const y = (row + 1) * h / 5;
    const rod = mesh(new THREE.CylinderGeometry(0.003 * H, 0.003 * H, w, 4), metal(0x8a8a80));
    rod.rotation.z = Math.PI / 2;
    rod.position.y = y;
    g.add(rod);
    for (let k = 0; k < 5; k++) {
      const b = mesh(new THREE.SphereGeometry(0.014 * H, 8, 6), glossy(beadCols[row]));
      b.scale.x = 0.7;
      b.position.set(-w / 2 + 0.03 * H + k * 0.022 * H, y, 0);
      b.userData = { row, k, base: b.position.x };
      g.add(b);
      beads.push(b);
    }
  }
  g.userData.beads = beads;
  g.userData.w = w;
  return g;
}

export function visor(api, rad) {
  const g = new THREE.Group();
  const band = mesh(new THREE.TorusGeometry(rad.x * 1.0, rad.x * 0.05, 6, 24), api.M(0x2a5a2a, { bump: 1 }));
  band.rotation.x = Math.PI / 2;
  g.add(band);
  const brim = new THREE.Mesh(new THREE.CylinderGeometry(rad.x * 0.9, rad.x * 0.9, rad.x * 0.03, 20, 1, false, -Math.PI / 2.4, Math.PI / 1.2), glass(0x3aff6a, 0.55));
  brim.position.set(0, 0, rad.z * 0.55);
  g.add(brim);
  return g;
}

// ------------------------------------------------------ more topic props --
// Compact primitives for the wider topic roster. `H` is the unscaled height,
// `rad` the head radii.

export function cloudPuffs(api, rad, n = 5) {
  // Cloud-like anatomy: lumpy cloud tufts growing out of the torso.
  const g = new THREE.Group();
  const mat = api.M(0xdfe6ee, { rough: 1, bump: 3 });
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + api.r.float(-0.3, 0.3);
    const el = api.r.float(-0.2, 0.6);
    const s = Math.min(rad.x, rad.y) * api.r.float(0.25, 0.4);
    const p = mesh(lumpy(new THREE.SphereGeometry(s, 12, 9), s * 0.08, 8 / s, i), mat);
    p.position.set(Math.sin(a) * Math.cos(el) * rad.x * 0.95, Math.sin(el) * rad.y * 0.95, Math.cos(a) * Math.cos(el) * rad.z * 0.95);
    g.add(p);
  }
  return g;
}

export function lightningHorns(rad) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0xffe14a, emissive: 0xffb000, emissiveIntensity: 0.9, roughness: 0.4 });
  for (const sx of [-1, 1]) {
    const bolt = new THREE.Group();
    const segs = [[0, 0, 0.06], [0.05, 0.08, 0.05], [-0.02, 0.15, 0.06]];
    for (const [x, y, len] of segs) {
      const s = mesh(new THREE.BoxGeometry(rad.x * 0.07, rad.x * len * 6, rad.x * 0.05), mat);
      s.position.set(x * rad.x * 3, y * rad.x * 5, 0);
      s.rotation.z = x > 0 ? -0.5 : 0.5;
      bolt.add(s);
    }
    bolt.position.set(sx * rad.x * 0.45, 0, 0);
    bolt.rotation.z = -sx * 0.35;
    g.add(bolt);
  }
  return g;
}

export function chefHat(api, rad) {
  const g = new THREE.Group();
  const mat = api.M(0xf6f2ea, { rough: 0.95, bump: 2 });
  const band = mesh(new THREE.CylinderGeometry(rad.x * 0.62, rad.x * 0.62, rad.y * 0.35, 18), mat);
  band.position.y = rad.y * 0.12;
  g.add(band);
  const puff = mesh(lumpy(new THREE.SphereGeometry(rad.x * 0.8, 16, 12), rad.x * 0.06, 10, 2), mat);
  puff.scale.set(1, 0.75, 1);
  puff.position.y = rad.y * 0.55;
  g.add(puff);
  return g;
}
export function ladle(H) {
  const g = new THREE.Group();
  const steel = metal(0xc8c8c0, { rough: 0.3, rust: false });
  const handle = mesh(new THREE.CylinderGeometry(0.008 * H, 0.008 * H, 0.32 * H, 6), steel);
  handle.position.y = 0.1 * H;
  g.add(handle);
  const bowl = mesh(new THREE.SphereGeometry(0.06 * H, 12, 8, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2), steel);
  bowl.material = steel.clone(); bowl.material.side = THREE.DoubleSide;
  bowl.position.y = 0.27 * H;
  bowl.rotation.x = Math.PI;
  g.add(bowl);
  const soup = mesh(new THREE.CircleGeometry(0.055 * H, 12), glossy(0x8a5a1a));
  soup.rotation.x = -Math.PI / 2;
  soup.position.y = 0.272 * H;
  g.add(soup);
  return g;
}

export function pithHelmet(api, rad) {
  const g = new THREE.Group();
  const mat = api.M(0xc8b080, { rough: 0.9, bump: 1.5 });
  const dome = mesh(new THREE.SphereGeometry(rad.x * 0.72, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat);
  g.add(dome);
  const brim = mesh(new THREE.CylinderGeometry(rad.x * 1.05, rad.x * 1.05, rad.x * 0.04, 20), mat);
  g.add(brim);
  return g;
}
export function suitcase(H) {
  const g = new THREE.Group();
  const box = mesh(lumpy(new THREE.BoxGeometry(0.26 * H, 0.2 * H, 0.08 * H, 3, 3, 2), 0.004 * H, 20, 1), clay(0x8a4a2a, { bump: 1.5 }));
  box.position.y = -0.12 * H;
  g.add(box);
  const handle = mesh(new THREE.TorusGeometry(0.035 * H, 0.008 * H, 6, 10, Math.PI), clay(0x2a1a10));
  handle.position.y = -0.02 * H;
  g.add(handle);
  for (const [x, y, c] of [[-0.06, -0.1, 0xd8c040], [0.07, -0.15, 0x4a8ad8]]) {
    const sticker = mesh(new THREE.CircleGeometry(0.03 * H, 10), new THREE.MeshBasicMaterial({ color: c }));
    sticker.position.set(x * H, y * H, 0.041 * H);
    g.add(sticker);
  }
  return g;
}

export function stethoscope(api, rad) {
  const g = new THREE.Group();
  const tube = mesh(new THREE.TorusGeometry(rad.x * 0.55, rad.x * 0.05, 6, 24, Math.PI * 1.3), rubber(0x1a1a1a));
  tube.rotation.set(Math.PI / 2, 0, -Math.PI * 0.15);
  g.add(tube);
  const bell = mesh(new THREE.CylinderGeometry(rad.x * 0.12, rad.x * 0.12, rad.x * 0.06, 12), metal(0xd0d0c8, { rust: false }));
  bell.position.set(0, -rad.x * 0.3, rad.x * 0.55);
  bell.rotation.x = Math.PI / 2;
  g.add(bell);
  return g;
}
export function sweatband(api, rad) {
  const band = mesh(new THREE.TorusGeometry(rad.x * 1.0, rad.x * 0.1, 8, 24), api.M(0xd8303a, { rough: 1, bump: 2 }));
  band.rotation.x = Math.PI / 2;
  return band;
}
export function dumbbell(H) {
  const g = new THREE.Group();
  const iron = metal(0x3a3a3a, { rough: 0.5 });
  const bar = mesh(new THREE.CylinderGeometry(0.008 * H, 0.008 * H, 0.2 * H, 6), iron);
  bar.rotation.z = Math.PI / 2;
  g.add(bar);
  for (const sx of [-1, 1]) {
    const w = mesh(new THREE.CylinderGeometry(0.04 * H, 0.04 * H, 0.04 * H, 10), iron);
    w.rotation.z = Math.PI / 2;
    w.position.x = sx * 0.1 * H;
    g.add(w);
  }
  return g;
}

export function headphones(rad) {
  const g = new THREE.Group();
  const band = mesh(new THREE.TorusGeometry(rad.x * 1.02, rad.x * 0.06, 6, 20, Math.PI), clay(0x2a2a2a));
  band.position.y = -rad.y * 0.15;
  g.add(band);
  for (const sx of [-1, 1]) {
    const cup = mesh(new THREE.CylinderGeometry(rad.x * 0.28, rad.x * 0.28, rad.x * 0.18, 14), clay(0xd8303a, { bump: 1 }));
    cup.rotation.z = Math.PI / 2;
    cup.position.set(sx * rad.x * 1.02, -rad.y * 0.2, 0);
    g.add(cup);
  }
  return g;
}
export function noteAntenna(rad) {
  const g = new THREE.Group();
  const black = clay(0x161616, { rough: 0.5 });
  const stem = mesh(new THREE.CylinderGeometry(rad.x * 0.03, rad.x * 0.03, rad.x * 0.7, 5), black);
  stem.position.y = rad.x * 0.35;
  g.add(stem);
  const head = mesh(new THREE.SphereGeometry(rad.x * 0.13, 10, 8), black);
  head.scale.set(1.3, 0.9, 0.8);
  head.position.set(-rad.x * 0.1, 0.02, 0);
  g.add(head);
  const flag = mesh(new THREE.BoxGeometry(rad.x * 0.25, rad.x * 0.06, rad.x * 0.03), black);
  flag.position.set(rad.x * 0.1, rad.x * 0.64, 0);
  flag.rotation.z = -0.5;
  g.add(flag);
  return g;
}

export function laptop(H) {
  const g = new THREE.Group();
  const grey = metal(0x9a9a98, { rough: 0.35, rust: false });
  const base = mesh(new THREE.BoxGeometry(0.26 * H, 0.012 * H, 0.18 * H), grey);
  g.add(base);
  const lid = new THREE.Group();
  lid.position.z = -0.09 * H;
  lid.rotation.x = -1.9;
  const screen = mesh(new THREE.BoxGeometry(0.26 * H, 0.012 * H, 0.18 * H), grey);
  screen.position.z = 0.09 * H;
  lid.add(screen);
  const glow = new THREE.Mesh(new THREE.PlaneGeometry(0.23 * H, 0.15 * H), new THREE.MeshBasicMaterial({ color: 0x4aff7a }));
  glow.rotation.x = -Math.PI / 2;
  glow.position.set(0, -0.007 * H, 0.09 * H);
  lid.add(glow);
  g.add(lid);
  g.rotation.x = 1.2;
  return g;
}
export function bulbAntennae(rad) {
  const g = new THREE.Group();
  for (const sx of [-1, 1]) {
    const stalk = mesh(new THREE.CylinderGeometry(rad.x * 0.03, rad.x * 0.04, rad.x * 0.6, 5), metal(0x6a6a6a));
    stalk.position.set(sx * rad.x * 0.3, rad.x * 0.3, 0);
    stalk.rotation.z = -sx * 0.3;
    g.add(stalk);
    const b = mesh(new THREE.SphereGeometry(rad.x * 0.1, 10, 8), glossy(0x4aff7a, { emissive: 0x2aff4a, ei: 0.9 }));
    b.position.set(sx * rad.x * 0.48, rad.x * 0.6, 0);
    g.add(b);
  }
  return g;
}

export function vikingHelmet(api, rad) {
  const g = new THREE.Group();
  const iron = metal(0x8a8478, { rough: 0.45 });
  const dome = mesh(new THREE.SphereGeometry(rad.x * 0.8, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2), iron);
  g.add(dome);
  const rim = mesh(new THREE.TorusGeometry(rad.x * 0.8, rad.x * 0.06, 6, 22), metal(0xb8893a, { rough: 0.35 }));
  rim.rotation.x = Math.PI / 2;
  g.add(rim);
  for (const sx of [-1, 1]) {
    const horn = mesh(sausage(rad.x * 0.7, [rad.x * 0.14, rad.x * 0.1, rad.x * 0.03]), api.M(0xece0c0, { rough: 0.6 }));
    horn.position.set(sx * rad.x * 0.7, rad.x * 0.1, 0);
    horn.rotation.z = sx * 2.2;
    g.add(horn);
  }
  return g;
}
export function scroll(H) {
  const g = new THREE.Group();
  const paper = clay(0xe8d8a8, { bump: 1 });
  const sheet = mesh(new THREE.BoxGeometry(0.2 * H, 0.24 * H, 0.004 * H), paper);
  sheet.position.y = 0.06 * H;
  g.add(sheet);
  for (const y of [-0.06, 0.18]) {
    const roll = mesh(new THREE.CylinderGeometry(0.018 * H, 0.018 * H, 0.22 * H, 8), clay(0x8a5a2a));
    roll.rotation.z = Math.PI / 2;
    roll.position.y = y * H;
    g.add(roll);
  }
  return g;
}

export function fishbowlHelmet(rad) {
  const g = new THREE.Group();
  const bowl = new THREE.Mesh(new THREE.SphereGeometry(rad.x * 1.45, 20, 16), glass(0xcfeaff, 0.18));
  bowl.position.y = -rad.y * 0.75;
  g.add(bowl);
  const collar = mesh(new THREE.TorusGeometry(rad.x * 0.9, rad.x * 0.1, 8, 24), metal(0xd0d0d0, { rust: false }));
  collar.rotation.x = Math.PI / 2;
  collar.position.y = -rad.y * 1.75;
  g.add(collar);
  return g;
}
export function rocket(H) {
  const g = new THREE.Group();
  const body = mesh(new THREE.CylinderGeometry(0.035 * H, 0.045 * H, 0.2 * H, 12), clay(0xe8e8e0, { bump: 1 }));
  body.position.y = 0.1 * H;
  g.add(body);
  const nose = mesh(new THREE.ConeGeometry(0.035 * H, 0.08 * H, 12), clay(0xd8303a));
  nose.position.y = 0.24 * H;
  g.add(nose);
  for (let i = 0; i < 3; i++) {
    const fin = mesh(new THREE.BoxGeometry(0.004 * H, 0.06 * H, 0.05 * H), clay(0xd8303a));
    fin.position.set(Math.sin(i * 2.1) * 0.045 * H, 0.02 * H, Math.cos(i * 2.1) * 0.045 * H);
    fin.rotation.y = i * 2.1;
    g.add(fin);
  }
  const flame = new THREE.Mesh(new THREE.ConeGeometry(0.03 * H, 0.08 * H, 8), new THREE.MeshBasicMaterial({ color: 0xffa020 }));
  flame.rotation.x = Math.PI;
  flame.position.y = -0.04 * H;
  g.add(flame);
  return g;
}

export function topHat(api, rad) {
  const g = new THREE.Group();
  const mat = api.M(0x1a1a1a, { rough: 0.6, bump: 1 });
  const crown = mesh(new THREE.CylinderGeometry(rad.x * 0.5, rad.x * 0.55, rad.y * 0.9, 18), mat);
  crown.position.y = rad.y * 0.45;
  g.add(crown);
  const brim = mesh(new THREE.CylinderGeometry(rad.x * 0.9, rad.x * 0.9, rad.x * 0.04, 20), mat);
  g.add(brim);
  const band = mesh(new THREE.CylinderGeometry(rad.x * 0.555, rad.x * 0.555, rad.y * 0.12, 18), clay(0xb8893a));
  band.position.y = rad.y * 0.1;
  g.add(band);
  g.rotation.z = 0.15;
  return g;
}
export function moneyBag(H) {
  const g = new THREE.Group();
  const sack = mesh(lumpy(new THREE.SphereGeometry(0.09 * H, 14, 10), 0.008 * H, 20, 4), clay(0xc8a868, { bump: 2 }));
  sack.scale.y = 1.1;
  sack.position.y = -0.1 * H;
  g.add(sack);
  const tie = mesh(new THREE.TorusGeometry(0.03 * H, 0.008 * H, 6, 12), clay(0x6a4a20));
  tie.rotation.x = Math.PI / 2;
  g.add(tie);
  const sign = mesh(new THREE.BoxGeometry(0.06 * H, 0.06 * H, 0.004 * H), new THREE.MeshBasicMaterial({ color: 0x2a6a2a }));
  sign.position.set(0, -0.1 * H, 0.095 * H);
  g.add(sign);
  return g;
}

export function ball(H, color = 0xe86a2a) {
  const b = mesh(new THREE.SphereGeometry(0.08 * H, 16, 12), clay(color, { bump: 1.5 }));
  b.position.y = 0.06 * H;
  const seam = mesh(new THREE.TorusGeometry(0.081 * H, 0.004 * H, 4, 24), clay(0x1a1a1a));
  seam.position.y = 0.06 * H;
  const g = new THREE.Group();
  g.add(b, seam);
  return g;
}

export function glasses3d(rad) {
  const g = new THREE.Group();
  const frame = mesh(new THREE.BoxGeometry(rad.x * 1.3, rad.x * 0.3, rad.x * 0.05), clay(0xf0f0f0));
  frame.position.z = rad.x * 0.1;
  g.add(frame);
  for (const [sx, c] of [[-1, 0xff2a2a], [1, 0x2ad8ff]]) {
    const lens = new THREE.Mesh(new THREE.PlaneGeometry(rad.x * 0.5, rad.x * 0.22), new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.75 }));
    lens.position.set(sx * rad.x * 0.32, 0, rad.x * 0.13);
    g.add(lens);
  }
  return g;
}
export function popcorn(H) {
  const g = new THREE.Group();
  const bucket = mesh(new THREE.CylinderGeometry(0.07 * H, 0.05 * H, 0.14 * H, 12, 1, true), clay(0xe83a3a));
  bucket.material.side = THREE.DoubleSide;
  bucket.position.y = 0.07 * H;
  g.add(bucket);
  for (let i = 0; i < 12; i++) {
    const k = mesh(lumpy(new THREE.SphereGeometry(0.018 * H, 6, 5), 0.004 * H, 60, i), clay(0xf8eec0, { bump: 2 }));
    k.position.set((Math.random() - 0.5) * 0.1 * H, 0.14 * H + Math.random() * 0.03 * H, (Math.random() - 0.5) * 0.1 * H);
    g.add(k);
  }
  return g;
}

export function catEars(api, rad) {
  const g = new THREE.Group();
  for (const sx of [-1, 1]) {
    const ear = mesh(new THREE.ConeGeometry(rad.x * 0.25, rad.x * 0.45, 4), api.M(0x8a6a4a, { bump: 3 }));
    ear.position.set(sx * rad.x * 0.5, rad.x * 0.1, 0);
    ear.rotation.z = -sx * 0.35;
    g.add(ear);
  }
  return g;
}
export function bone(H) {
  const g = new THREE.Group();
  const m = clay(0xf0e8d0, { bump: 1 });
  const shaft = mesh(new THREE.CylinderGeometry(0.014 * H, 0.014 * H, 0.18 * H, 8), m);
  shaft.position.y = 0.06 * H;
  g.add(shaft);
  for (const y of [-0.03, 0.15]) for (const sx of [-1, 1]) {
    const knob = mesh(new THREE.SphereGeometry(0.022 * H, 8, 6), m);
    knob.position.set(sx * 0.016 * H, y * H, 0);
    g.add(knob);
  }
  return g;
}

export function flowerPotHat(api, rad) {
  const g = new THREE.Group();
  const pot = mesh(new THREE.CylinderGeometry(rad.x * 0.5, rad.x * 0.38, rad.y * 0.5, 14), api.M(0xb8603a, { bump: 2 }));
  pot.position.y = rad.y * 0.25;
  g.add(pot);
  const soil = mesh(new THREE.CircleGeometry(rad.x * 0.48, 14), clay(0x3a2a1a));
  soil.rotation.x = -Math.PI / 2;
  soil.position.y = rad.y * 0.5;
  g.add(soil);
  const stem = mesh(new THREE.CylinderGeometry(rad.x * 0.03, rad.x * 0.03, rad.y * 0.8, 5), clay(0x3a8a2a));
  stem.position.y = rad.y * 0.9;
  g.add(stem);
  for (let i = 0; i < 6; i++) {
    const petal = mesh(new THREE.SphereGeometry(rad.x * 0.12, 8, 6), clay(0xf0d030));
    petal.scale.set(1, 0.4, 0.6);
    petal.position.set(Math.cos(i) * rad.x * 0.15, rad.y * 1.3, Math.sin(i) * rad.x * 0.15);
    g.add(petal);
  }
  const center = mesh(new THREE.SphereGeometry(rad.x * 0.09, 8, 6), clay(0x6a3a1a));
  center.position.y = rad.y * 1.32;
  g.add(center);
  return g;
}
export function wateringCan(H) {
  const g = new THREE.Group();
  const tin = metal(0x5a8a7a, { rough: 0.45 });
  const body = mesh(new THREE.CylinderGeometry(0.06 * H, 0.07 * H, 0.1 * H, 12), tin);
  body.position.y = -0.08 * H;
  g.add(body);
  const spout = mesh(new THREE.CylinderGeometry(0.008 * H, 0.014 * H, 0.14 * H, 6), tin);
  spout.position.set(0.09 * H, -0.05 * H, 0);
  spout.rotation.z = -1.0;
  g.add(spout);
  const handle = mesh(new THREE.TorusGeometry(0.04 * H, 0.008 * H, 6, 10, Math.PI), tin);
  handle.position.y = -0.03 * H;
  g.add(handle);
  return g;
}

export function speechSign(H, text = 'ABC') {
  const g = new THREE.Group();
  const cv = document.createElement('canvas');
  cv.width = 128; cv.height = 96;
  const c = cv.getContext('2d');
  c.fillStyle = '#f4eedc'; c.beginPath(); c.roundRect(4, 4, 120, 70, 18); c.fill();
  c.beginPath(); c.moveTo(30, 70); c.lineTo(20, 92); c.lineTo(50, 72); c.fill();
  c.fillStyle = '#2a1a10'; c.font = 'bold 34px sans-serif'; c.textAlign = 'center'; c.fillText(text, 64, 52);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(0.24 * H, 0.18 * H), new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide }));
  sign.position.y = 0.2 * H;
  g.add(sign);
  const stick = mesh(new THREE.CylinderGeometry(0.006 * H, 0.006 * H, 0.16 * H, 5), clay(0x6a4020));
  stick.position.y = 0.06 * H;
  g.add(stick);
  return g;
}

export function heartAntenna(rad) {
  const g = new THREE.Group();
  const red = glossy(0xe02a4a, { emissive: 0x6a0010, ei: 0.5 });
  const stem = mesh(new THREE.CylinderGeometry(rad.x * 0.025, rad.x * 0.025, rad.x * 0.6, 5), clay(0x1a1a1a));
  stem.position.y = rad.x * 0.3;
  g.add(stem);
  const heart = new THREE.Group();
  for (const sx of [-1, 1]) {
    const lobe = mesh(new THREE.SphereGeometry(rad.x * 0.12, 10, 8), red);
    lobe.position.set(sx * rad.x * 0.08, 0, 0);
    heart.add(lobe);
  }
  const tip = mesh(new THREE.ConeGeometry(rad.x * 0.17, rad.x * 0.22, 10), red);
  tip.rotation.x = Math.PI;
  tip.position.y = -rad.x * 0.12;
  heart.add(tip);
  heart.position.y = rad.x * 0.72;
  g.add(heart);
  return g;
}
export function rose(H) {
  const g = new THREE.Group();
  const stem = mesh(new THREE.CylinderGeometry(0.005 * H, 0.005 * H, 0.22 * H, 5), clay(0x2a6a2a));
  stem.position.y = 0.06 * H;
  g.add(stem);
  const bloom = mesh(lumpy(new THREE.SphereGeometry(0.035 * H, 10, 8), 0.006 * H, 80, 7), clay(0xc8102a, { bump: 3 }));
  bloom.position.y = 0.19 * H;
  g.add(bloom);
  return g;
}

export function crown(api, rad) {
  const g = new THREE.Group();
  const gold = metal(0xd8a830, { rough: 0.3, rust: false });
  const band = mesh(new THREE.CylinderGeometry(rad.x * 0.55, rad.x * 0.55, rad.y * 0.2, 16, 1, true), gold);
  band.material = gold.clone(); band.material.side = THREE.DoubleSide;
  band.position.y = rad.y * 0.1;
  g.add(band);
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2;
    const spike = mesh(new THREE.ConeGeometry(rad.x * 0.08, rad.y * 0.25, 4), gold);
    spike.position.set(Math.sin(a) * rad.x * 0.55, rad.y * 0.3, Math.cos(a) * rad.x * 0.55);
    g.add(spike);
  }
  g.rotation.z = -0.2;
  return g;
}
export function gamepad(H) {
  const g = new THREE.Group();
  const body = mesh(lumpy(new THREE.CapsuleGeometry(0.035 * H, 0.1 * H, 6, 10), 0.003 * H, 30, 2), clay(0x3a3a4a, { bump: 1 }));
  body.rotation.z = Math.PI / 2;
  g.add(body);
  [[0.04, 0.012, 0xe02a2a], [0.055, -0.005, 0x2ae05a], [0.025, -0.005, 0x2a6ae0]].forEach(([x, y, c]) => {
    const btn = mesh(new THREE.SphereGeometry(0.01 * H, 6, 5), glossy(c));
    btn.position.set(x * H, y * H, 0.03 * H);
    g.add(btn);
  });
  return g;
}
