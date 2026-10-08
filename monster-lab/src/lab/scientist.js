// Dr. Stitchwick: a hunched puppet with a huge egg head, a brass loupe, a
// hunchback, a stained coat and enormous rubber gloves.
//
// Animation model: actions output a Pose (root transform, spine, look target,
// world-space wrist targets + hand orientations, feet targets). Poses blend on
// action change and are applied with 2-bone IK. The pose is only re-evaluated
// 12 times per second, giving the character a stop-motion rhythm while the
// camera stays perfectly smooth.
import * as THREE from 'three';
import { clay, cloth, rubber, metal, glossy, glass } from '../three/materials.js';
import { textures } from '../three/textures.js';
import { lumpy, sausage, mesh, stitches, ellipsoidLine } from '../three/geom.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const FPS = 12;
const UPPER = 0.33, FORE = 0.32, THIGH = 0.37, SHIN = 0.37;
const HIP_Y = 0.78;

// ---------------------------------------------------------------- pose ----
export function makePose() {
  return {
    rootPos: V(), rootYaw: 0,
    hipsY: HIP_Y, hipsX: 0,
    spineX: 0.22, spineY: 0, spineZ: 0,
    look: V(0, 1.4, 4), headTilt: 0,
    jaw: 0, brow: 0, blink: 0,
    hR: { p: V(), q: new THREE.Quaternion(), curl: 0.3 },
    hL: { p: V(), q: new THREE.Quaternion(), curl: 0.3 },
    fR: V(), fL: V(),
  };
}

export function copyPose(o, a) {
  o.rootPos.copy(a.rootPos); o.rootYaw = a.rootYaw;
  o.hipsY = a.hipsY; o.hipsX = a.hipsX;
  o.spineX = a.spineX; o.spineY = a.spineY; o.spineZ = a.spineZ;
  o.look.copy(a.look); o.headTilt = a.headTilt;
  o.jaw = a.jaw; o.brow = a.brow; o.blink = a.blink;
  for (const h of ['hR', 'hL']) { o[h].p.copy(a[h].p); o[h].q.copy(a[h].q); o[h].curl = a[h].curl; }
  o.fR.copy(a.fR); o.fL.copy(a.fL);
  return o;
}

const lerp = (a, b, t) => a + (b - a) * t;
function lerpAngle(a, b, t) {
  let d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * t;
}
export function blendPose(o, a, b, t) {
  o.rootPos.lerpVectors(a.rootPos, b.rootPos, t); o.rootYaw = lerpAngle(a.rootYaw, b.rootYaw, t);
  o.hipsY = lerp(a.hipsY, b.hipsY, t); o.hipsX = lerp(a.hipsX, b.hipsX, t);
  o.spineX = lerp(a.spineX, b.spineX, t); o.spineY = lerp(a.spineY, b.spineY, t); o.spineZ = lerp(a.spineZ, b.spineZ, t);
  o.look.lerpVectors(a.look, b.look, t); o.headTilt = lerp(a.headTilt, b.headTilt, t);
  o.jaw = lerp(a.jaw, b.jaw, t); o.brow = lerp(a.brow, b.brow, t); o.blink = lerp(a.blink, b.blink, t);
  for (const h of ['hR', 'hL']) {
    o[h].p.lerpVectors(a[h].p, b[h].p, t);
    o[h].q.slerpQuaternions(a[h].q, b[h].q, t);
    o[h].curl = lerp(a[h].curl, b[h].curl, t);
  }
  o.fR.lerpVectors(a.fR, b.fR, t); o.fL.lerpVectors(a.fL, b.fL, t);
  return o;
}

// Hand orientation from finger direction + palm normal (world space).
const _bx = V(), _by = V(), _bz = V(), _bm = new THREE.Matrix4();
export function handQuat(fingers, palm, out = new THREE.Quaternion()) {
  _by.copy(fingers).normalize().negate();
  _bz.copy(palm).addScaledVector(_by, -palm.dot(_by)).normalize();
  _bx.crossVectors(_by, _bz).normalize();
  _bm.makeBasis(_bx, _by, _bz);
  return out.setFromRotationMatrix(_bm);
}

export const smooth = (t) => { t = Math.min(1, Math.max(0, t)); return t * t * (3 - 2 * t); };
export const seg = (t, a, b) => smooth((t - a) / (b - a));

// ----------------------------------------------------------------- rig ----
export class Scientist {
  constructor() {
    this.root = new THREE.Group();
    this.root.name = 'scientist';
    this.pose = makePose();
    this._prev = makePose();
    this._raw = makePose();
    this.action = null;
    this.timeScale = 1;
    this._step = 0;
    this._stepAcc = 1;
    this.held = { R: null, L: null };
    this._build();
  }

  _build() {
    const t = textures();
    const skin = clay(0xd9b99a, { bump: 2.6 });
    const ruddy = clay(0xc9786a, { bump: 2 });
    const coat = cloth(0xffffff, { map: t.cloth });
    const trousers = cloth(0x3b342c);
    const glove = rubber(0xe3b53a);
    const boot = rubber(0x221d18);
    const hair = clay(0xf1ece0, { rough: 1, bump: 4 });
    const apron = clay(0x6a3a2a, { rough: 0.55, bump: 1.5, map: t.cloth });
    apron.color.set(0x8a5a44);
    const dark = new THREE.MeshStandardMaterial({ color: 0x1a0a08, roughness: 0.7 });
    const tooth = clay(0xe8d9a8, { rough: 0.5, bump: 0.6 });
    const brass = metal(0xb8893a, { rough: 0.35 });

    const root = this.root;
    const hips = (this.hips = new THREE.Group());
    hips.position.y = HIP_Y;
    root.add(hips);

    // Torso + hunch + belly.
    const spine = (this.spine = new THREE.Group());
    spine.position.y = 0.02;
    hips.add(spine);
    const torsoG = sausage(0.56, [0.19, 0.23, 0.21, 0.16, 0.12]);
    torsoG.rotateX(Math.PI);
    spine.add(mesh(lumpy(torsoG, 0.008, 5, 3), coat));
    const belly = mesh(lumpy(new THREE.SphereGeometry(0.19, 20, 16), 0.01, 5, 4), coat);
    belly.position.set(0, 0.14, 0.07);
    spine.add(belly);
    const hump = mesh(lumpy(new THREE.SphereGeometry(0.16, 18, 14), 0.012, 5, 5), coat);
    hump.position.set(0.03, 0.42, -0.1);
    hump.scale.set(1.1, 0.9, 1);
    spine.add(hump);
    // Coat skirt flaring down past the knees.
    const skirt = mesh(new THREE.CylinderGeometry(0.2, 0.29, 0.5, 20, 4, true), coat);
    skirt.material = coat.clone();
    skirt.material.side = THREE.DoubleSide;
    skirt.position.y = -0.2;
    lumpy(skirt.geometry, 0.012, 6, 6);
    spine.add(skirt);
    // Rubber apron.
    const ap = mesh(new THREE.CylinderGeometry(0.245, 0.31, 0.78, 16, 3, true, -0.85, 1.7), apron);
    ap.material.side = THREE.DoubleSide;
    ap.position.set(0, 0.0, 0.035);
    spine.add(ap);
    const bib = mesh(new THREE.CylinderGeometry(0.2, 0.235, 0.25, 12, 1, true, -0.6, 1.2), apron);
    bib.position.set(0, 0.47, 0.06);
    spine.add(bib);
    // Chest pocket with test tubes.
    const tubeMat = glass(0x8cf05a, 0.6);
    for (let i = 0; i < 2; i++) {
      const tube = mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.1, 8), tubeMat);
      tube.position.set(-0.08 - i * 0.03, 0.44, 0.15);
      tube.rotation.z = 0.1 - i * 0.2;
      spine.add(tube);
    }

    const chest = (this.chest = new THREE.Group());
    chest.position.y = 0.5;
    spine.add(chest);
    // Collar points.
    for (const sx of [-1, 1]) {
      const c = mesh(new THREE.ConeGeometry(0.05, 0.12, 4), coat);
      c.position.set(sx * 0.07, 0.04, 0.07);
      c.rotation.set(-1.0, 0, sx * 0.6);
      chest.add(c);
    }

    // Neck + head.
    const neck = (this.neck = new THREE.Group());
    neck.position.set(0, 0.03, 0.06);
    chest.add(neck);
    const neckM = mesh(sausage(0.12, [0.045, 0.04]), skin);
    neckM.rotation.x = Math.PI - 0.5;
    neck.add(neckM);
    const head = (this.head = new THREE.Group());
    head.position.set(0, 0.08, 0.05);
    neck.add(head);
    this._buildHead(head, { skin, ruddy, hair, dark, tooth, brass });

    // Arms.
    this.arms = {};
    for (const [key, side] of [['R', -1], ['L', 1]]) {
      const shoulder = new THREE.Group();
      shoulder.position.set(side * 0.17, 0.0, -0.02);
      chest.add(shoulder);
      const pad = mesh(new THREE.SphereGeometry(0.075, 14, 10), coat);
      shoulder.add(pad);
      const upper = new THREE.Group();
      shoulder.add(upper);
      upper.add(mesh(lumpy(sausage(UPPER, [0.066, 0.06, 0.062]), 0.006, 8, side + 9), coat));
      const fore = new THREE.Group();
      fore.position.y = -UPPER;
      upper.add(fore);
      const cuff = mesh(new THREE.TorusGeometry(0.058, 0.022, 8, 16), coat);
      cuff.rotation.x = Math.PI / 2;
      cuff.position.y = -0.05;
      fore.add(cuff);
      fore.add(mesh(sausage(FORE * 0.6, [0.034, 0.03]), skin));
      const gloveCuff = mesh(new THREE.CylinderGeometry(0.058, 0.04, 0.13, 12, 1, true), glove);
      gloveCuff.material = glove.clone();
      gloveCuff.material.side = THREE.DoubleSide;
      gloveCuff.position.y = -FORE + 0.06;
      fore.add(gloveCuff);
      const hand = new THREE.Group();
      hand.position.y = -FORE;
      fore.add(hand);
      const fingers = this._buildHand(hand, side, glove);
      const grip = new THREE.Group();
      grip.position.set(0, -0.085, 0.035);
      hand.add(grip);
      this.arms[key] = { shoulder, upper, fore, hand, fingers, grip, side };
    }

    // Legs.
    this.legs = {};
    for (const [key, side] of [['R', -1], ['L', 1]]) {
      const thigh = new THREE.Group();
      thigh.position.set(side * 0.11, -0.04, 0);
      hips.add(thigh);
      thigh.add(mesh(lumpy(sausage(THIGH, [0.07, 0.06, 0.052]), 0.006, 7, side), trousers));
      const shin = new THREE.Group();
      shin.position.y = -THIGH;
      thigh.add(shin);
      shin.add(mesh(sausage(SHIN, [0.052, 0.045, 0.05]), trousers));
      const foot = new THREE.Group();
      foot.position.y = -SHIN;
      shin.add(foot);
      const b = mesh(lumpy(new THREE.SphereGeometry(1, 16, 12), 0.04, 3, side + 2), boot);
      b.scale.set(0.075, 0.06, 0.15);
      b.position.set(0, -0.025, 0.06);
      foot.add(b);
      const sole = mesh(new THREE.BoxGeometry(0.13, 0.025, 0.27), boot);
      sole.position.set(0, -0.07, 0.06);
      foot.add(sole);
      this.legs[key] = { thigh, shin, foot, side };
    }

    root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  }

  _buildHead(head, { skin, ruddy, hair, dark, tooth, brass }) {
    // Egg cranium, wider and taller at the back-top.
    const g = new THREE.SphereGeometry(1, 32, 24);
    g.scale(0.205, 0.25, 0.215);
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const y = p.getY(i), z = p.getZ(i);
      const f = 1 + 0.22 * Math.max(0, y / 0.25) - 0.08 * Math.max(0, -y / 0.25);
      p.setXYZ(i, p.getX(i) * f, y, z * f - 0.03 * (y / 0.25));
    }
    lumpy(g, 0.008, 6, 21);
    const cranium = mesh(g, skin);
    cranium.position.set(0, 0.24, 0);
    head.add(cranium);
    // Scar across the scalp, projected onto the actual cranium surface.
    cranium.updateMatrixWorld(true);
    const ray = new THREE.Raycaster();
    const scar = [];
    for (let i = 0; i < 10; i++) {
      const u = i / 9;
      const d = V(-0.75 + u * 1.4, 0.95, 0.35 - u * 0.95).normalize();
      ray.set(cranium.position.clone().add(d), d.clone().negate());
      const hit = ray.intersectObject(cranium, false)[0];
      if (hit) scar.push({ p: hit.point.clone(), n: hit.face.normal.clone(), t: null });
    }
    for (let i = 0; i < scar.length; i++) scar[i].t = scar[Math.min(scar.length - 1, i + 1)].p.clone().sub(scar[Math.max(0, i - 1)].p).normalize();
    head.add(stitches(scar, { len: 0.045 }));
    const scarLine = mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(scar.map((q) => q.p.clone().addScaledVector(q.n, -0.002))), 30, 0.006, 5), ruddy, { cast: false });
    head.add(scarLine);

    // Ears.
    for (const sx of [-1, 1]) {
      const ear = mesh(lumpy(new THREE.SphereGeometry(0.075, 14, 10), 0.006, 14, sx), skin);
      ear.scale.set(0.35, 1, 0.7);
      ear.position.set(sx * 0.215, 0.2, -0.01);
      ear.rotation.z = -sx * 0.35;
      head.add(ear);
    }
    // Wild white hair: clumps of tufts bursting from the sides and back.
    for (let i = 0; i < 34; i++) {
      const sx = i % 2 ? 1 : -1;
      const k = Math.floor(i / 2) / 16;
      const ang = -0.3 + k * 2.2; // around the back of the head
      const len = 0.11 + ((i * 7) % 5) * 0.035;
      const tuft = mesh(new THREE.ConeGeometry(0.03 + (i % 3) * 0.008, len, 5), hair);
      const px = sx * (0.19 * Math.cos(ang * 0.6) + 0.03);
      const pz = -0.02 - Math.sin(ang) * 0.17;
      const py = 0.2 + Math.cos(ang * 1.3) * 0.08 + (i % 4) * 0.012;
      tuft.position.set(px, py, pz);
      const out = V(px * 1.6, 0.35 + (i % 3) * 0.2, pz - 0.05).normalize();
      tuft.quaternion.setFromUnitVectors(V(0, 1, 0), out);
      tuft.translateY(len * 0.35);
      head.add(tuft);
    }

    // Nose: long and hooked.
    const nose = mesh(sausage(0.16, [0.03, 0.05, 0.045, 0.03]), ruddy);
    nose.position.set(0, 0.25, 0.2);
    nose.rotation.x = -1.0;
    head.add(nose);

    // Left eye (character's left, +x) behind a brass loupe; magnified.
    const eyeWhite = glossy(0xf2ecd8);
    const iris = glossy(0x4a9a3a);
    const pupil = glossy(0x050505);
    const mkEye = (r) => {
      const e = new THREE.Group();
      e.add(mesh(new THREE.SphereGeometry(r, 16, 12), eyeWhite, { cast: false }));
      const look = new THREE.Group();
      const ir = mesh(new THREE.SphereGeometry(r * 0.55, 12, 8), iris, { cast: false });
      ir.scale.z = 0.4; ir.position.z = r * 0.9;
      const pu = mesh(new THREE.SphereGeometry(r * 0.3, 10, 8), pupil, { cast: false });
      pu.scale.z = 0.4; pu.position.z = r * 0.99;
      const hl = new THREE.Mesh(new THREE.SphereGeometry(r * 0.1, 6, 4), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      hl.position.set(r * 0.2, r * 0.25, r * 1.02);
      look.add(ir, pu, hl);
      e.add(look);
      return { e, look };
    };
    const big = mkEye(0.058);
    big.e.position.set(0.085, 0.29, 0.155);
    head.add(big.e);
    const loupe = mesh(new THREE.CylinderGeometry(0.07, 0.064, 0.07, 18, 1, true), brass);
    loupe.material = brass.clone(); loupe.material.side = THREE.DoubleSide;
    loupe.rotation.x = Math.PI / 2;
    loupe.position.set(0.085, 0.29, 0.2);
    head.add(loupe);
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.066, 20), glass(0xe8f4ff, 0.22));
    lens.position.set(0.085, 0.29, 0.236);
    head.add(lens);
    const rimF = mesh(new THREE.TorusGeometry(0.068, 0.009, 6, 20), brass);
    rimF.position.set(0.085, 0.29, 0.236);
    head.add(rimF);
    const strap = mesh(new THREE.TorusGeometry(0.235, 0.012, 6, 30), new THREE.MeshStandardMaterial({ color: 0x3a2618, roughness: 0.8 }));
    strap.rotation.set(Math.PI / 2 + 0.15, 0, 0);
    strap.position.set(0, 0.3, -0.01);
    head.add(strap);

    const small = mkEye(0.037);
    small.e.position.set(-0.085, 0.285, 0.198);
    head.add(small.e);
    this.eyes = [big, small];

    // Eyelids for blinking (squinty on the small eye).
    this.lids = [];
    for (const [e, r, open] of [[big.e, 0.062, -0.5], [small.e, 0.041, 0.15]]) {
      const lid = mesh(new THREE.SphereGeometry(r, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), skin, { cast: false });
      lid.rotation.x = open;
      e.add(lid);
      this.lids.push({ lid, open });
    }
    // Brows.
    this.brows = [];
    for (const [x, y, rz] of [[0.085, 0.36, -0.2], [-0.085, 0.33, 0.35]]) {
      const b = mesh(sausage(0.1, [0.02, 0.028, 0.018]), hair);
      b.rotation.z = Math.PI / 2 + rz;
      b.position.set(x + 0.05, y, 0.17);
      head.add(b);
      this.brows.push({ b, y, rz });
    }

    // Mouth: dark grin cavity, crooked upper teeth, hinged chin.
    const cav = mesh(new THREE.SphereGeometry(1, 16, 10), dark, { cast: false });
    const MC = V(0, 0.115, 0.168), MR = V(0.095, 0.026, 0.04);
    cav.scale.copy(MR);
    cav.position.copy(MC);
    cav.rotation.z = 0.06;
    head.add(cav);
    this.mouth = cav;
    const offs = [-0.068, -0.042, -0.015, 0.014, 0.04, 0.066];
    offs.forEach((x, i) => {
      if (i === 3) return; // missing tooth
      const h = 0.022 + (i % 2) * 0.008;
      const t = mesh(new THREE.BoxGeometry(0.02, h, 0.01), tooth, { cast: false });
      const front = MC.z + MR.z * Math.sqrt(Math.max(0, 1 - (x / MR.x) ** 2));
      t.position.set(x, MC.y + MR.y * 0.75 - h / 2, front + 0.002);
      t.rotation.set(0, x * 5, (i - 2.5) * 0.09);
      head.add(t);
    });
    const jaw = (this.jaw = new THREE.Group());
    jaw.position.set(0, 0.12, 0.0);
    head.add(jaw);
    const chin = mesh(lumpy(new THREE.SphereGeometry(1, 18, 12), 0.05, 4, 31), skin);
    chin.scale.set(0.13, 0.065, 0.13);
    chin.position.set(0, -0.045, 0.08);
    jaw.add(chin);
    const point = mesh(new THREE.ConeGeometry(0.035, 0.09, 8), skin);
    point.position.set(0, -0.09, 0.17);
    point.rotation.x = 2.4;
    jaw.add(point);
    for (const x of [-0.05, 0.035]) {
      const t = mesh(new THREE.ConeGeometry(0.012, 0.035, 5), tooth, { cast: false });
      t.position.set(x, -0.012, 0.19);
      jaw.add(t);
    }
  }

  _buildHand(hand, side, glove) {
    const palm = mesh(lumpy(new THREE.SphereGeometry(1, 14, 10), 0.08, 3, side + 40), glove);
    palm.scale.set(0.058, 0.065, 0.032);
    palm.position.y = -0.05;
    hand.add(palm);
    const fingers = [];
    for (let i = 0; i < 4; i++) {
      const x = (i - 1.5) * 0.026;
      const f1 = new THREE.Group();
      f1.position.set(x, -0.1, 0.005);
      f1.rotation.z = (i - 1.5) * 0.08;
      hand.add(f1);
      const len = i === 0 || i === 3 ? 0.04 : 0.048;
      f1.add(mesh(sausage(len, [0.016, 0.015]), glove));
      const f2 = new THREE.Group();
      f2.position.y = -len;
      f1.add(f2);
      f2.add(mesh(sausage(len * 0.85, [0.015, 0.013]), glove));
      fingers.push(f1, f2);
    }
    // Thumb on the outer side: -x for the right hand, +x for the left.
    const th = new THREE.Group();
    th.position.set(-side * -0.045, -0.04, 0.02);
    th.rotation.set(0.4, 0, side * -0.9);
    hand.add(th);
    th.add(mesh(sausage(0.06, [0.018, 0.015]), glove));
    fingers.thumb = th;
    return fingers;
  }

  // Grip world position for a hand, given a wrist pose (used to place tool tips).
  static gripOffset() { return V(0, -0.085, 0.035); }

  // --------------------------------------------------------------- props ----
  hold(key, obj, { offset = null, quat = null } = {}) {
    const g = this.arms[key].grip;
    this.held[key] = obj;
    g.add(obj);
    obj.position.copy(offset || V());
    obj.quaternion.copy(quat || new THREE.Quaternion());
  }
  release(key, scene) {
    const obj = this.held[key];
    if (!obj) return null;
    this.held[key] = null;
    scene.attach(obj);
    return obj;
  }

  // ----------------------------------------------------------- actions ----
  // action: { name, duration, loop?, blend?, pose(t, pose), events?: [{t, fn}] }
  play(action) {
    if (this.action?._resolve) this.action._resolve('interrupted');
    copyPose(this._prev, this.pose);
    this.action = { ...action, t: 0, fired: new Set() };
    this._stepAcc = 1; // re-evaluate immediately
    return new Promise((res) => { this.action._resolve = res; });
  }

  update(dt) {
    const a = this.action;
    if (!a) return;
    const prevT = a.t;
    a.t += dt * this.timeScale;
    // Events fire on the smooth clock so props sync to lifecycle precisely.
    if (a.events) {
      for (let i = 0; i < a.events.length; i++) {
        const e = a.events[i];
        if (!a.fired.has(i) && a.t >= e.t && prevT <= e.t + 1e9) { a.fired.add(i); e.fn(); }
      }
    }
    this._stepAcc += dt;
    if (this._stepAcc >= 1 / FPS) {
      this._stepAcc %= 1 / FPS;
      this._step++;
      const t = a.loop ? a.t : Math.min(a.t, a.duration);
      a.pose(t, this._raw);
      const w = smooth(t / (a.blend ?? 0.3));
      blendPose(this.pose, this._prev, this._raw, w);
      this.apply(this.pose);
    }
    if (!a.loop && a.t >= a.duration && a._resolve) {
      const r = a._resolve;
      a._resolve = null;
      r('done');
    }
  }

  // Stand at `pos` facing `yaw`: feet planted, arms relaxed, looking at `look`.
  stand(p, pos, yaw, look) {
    p.rootPos.copy(pos); p.rootYaw = yaw;
    p.hipsY = HIP_Y; p.hipsX = 0;
    p.spineX = 0.22; p.spineY = 0; p.spineZ = 0;
    p.look.copy(look); p.headTilt = 0; p.jaw = 0; p.brow = 0; p.blink = 0;
    const L = (x, y, z) => this.local(pos, yaw, x, y, z);
    p.fR.copy(L(-0.12, 0.07, 0.0)); p.fL.copy(L(0.12, 0.07, 0.02));
    p.hR.p.copy(L(-0.25, 0.86, 0.12)); p.hL.p.copy(L(0.25, 0.86, 0.12));
    const fwd = this.dir(yaw, 0, 0, 1), right = this.dir(yaw, 1, 0, 0);
    handQuat(V(0, -1, 0).addScaledVector(fwd, 0.3), right.clone(), p.hR.q);
    handQuat(V(0, -1, 0).addScaledVector(fwd, 0.3), right.clone().negate(), p.hL.q);
    p.hR.curl = 0.35; p.hL.curl = 0.35;
    return p;
  }

  local(pos, yaw, x, y, z) {
    const c = Math.cos(yaw), s = Math.sin(yaw);
    return V(pos.x + x * c + z * s, pos.y + y, pos.z - x * s + z * c);
  }
  dir(yaw, x, y, z) {
    const c = Math.cos(yaw), s = Math.sin(yaw);
    return V(x * c + z * s, y, -x * s + z * c);
  }

  // --------------------------------------------------------------- apply ----
  apply(p) {
    const jitter = (k) => (Math.sin(this._step * 12.9898 + k * 78.233) * 43758.5453 % 1) * 0.008;
    this.root.position.copy(p.rootPos);
    this.root.rotation.y = p.rootYaw;
    this.hips.position.set(p.hipsX, p.hipsY, 0);
    this.spine.rotation.set(p.spineX + jitter(1), p.spineY + jitter(2), p.spineZ + jitter(3));
    this.root.updateMatrixWorld(true);

    // Head look-at in the neck's parent (chest) space, clamped.
    const chestInv = new THREE.Matrix4().copy(this.chest.matrixWorld).invert();
    const lp = p.look.clone().applyMatrix4(chestInv).sub(this.neck.position);
    const yaw = THREE.MathUtils.clamp(Math.atan2(lp.x, lp.z), -1.1, 1.1);
    const pitch = THREE.MathUtils.clamp(-Math.atan2(lp.y - 0.25, Math.hypot(lp.x, lp.z)), -0.6, 1.0);
    this.neck.rotation.set(pitch * 0.4, yaw * 0.4, 0);
    this.head.rotation.set(pitch * 0.6 + jitter(4), yaw * 0.6, p.headTilt);
    this.jaw.rotation.x = p.jaw * 0.55;
    this.mouth.scale.y = 0.026 + p.jaw * 0.05;
    for (const l of this.lids) l.lid.rotation.x = lerp(l.open, Math.PI / 2, p.blink);
    this.brows.forEach((b, i) => {
      b.b.position.y = b.y - p.brow * 0.025;
      b.b.rotation.z = Math.PI / 2 + b.rz + (i === 0 ? -1 : 1) * p.brow * 0.3;
    });
    this.root.updateMatrixWorld(true);

    // Arms.
    for (const k of ['R', 'L']) {
      const arm = this.arms[k];
      const h = p['h' + k];
      const pole = this.local(p.rootPos, p.rootYaw, arm.side * 0.9, 0.9, -0.6);
      solveTwoBone(arm.upper, arm.fore, UPPER, FORE, h.p, pole);
      setWorldQuat(arm.hand, h.q, arm.fore);
      const c = h.curl;
      arm.fingers.forEach((f, i) => { f.rotation.x = -c * (i % 2 ? 1.25 : 1.0); });
      arm.fingers.thumb.rotation.x = 0.4 + c * 0.5;
    }
    // Legs: knees point forward.
    for (const k of ['R', 'L']) {
      const leg = this.legs[k];
      const pole = this.local(p.rootPos, p.rootYaw, leg.side * 0.3, 0.5, 1.5);
      solveTwoBone(leg.thigh, leg.shin, THIGH, SHIN, p['f' + k], pole);
      const q = new THREE.Quaternion().setFromAxisAngle(V(0, 1, 0), p.rootYaw + leg.side * 0.25);
      setWorldQuat(leg.foot, q, leg.shin);
    }
    this.root.updateMatrixWorld(true);
  }

  gripWorld(key) { return this.arms[key].grip.getWorldPosition(V()); }
}

// ------------------------------------------------------------------ IK ----
const _S = V(), _D = V(), _n = V(), _E = V(), _T = V(), _X = V(), _Y = V(), _Z = V();
const _m4 = new THREE.Matrix4(), _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion(), _qp = new THREE.Quaternion();

function setWorldQuat(obj, qWorld, parent) {
  parent.getWorldQuaternion(_qp);
  obj.quaternion.copy(_qp.invert().multiply(qWorld));
  obj.updateMatrixWorld(true);
}

// Bones point down their local -Y. Solves upper/lower so the end reaches `target`.
function solveTwoBone(upper, lower, a, b, target, pole) {
  upper.parent.updateMatrixWorld(true);
  upper.getWorldPosition(_S);
  _D.subVectors(target, _S);
  let d = _D.length();
  const dir = _D.normalize();
  d = THREE.MathUtils.clamp(d, Math.abs(a - b) + 0.01, a + b - 0.002);
  const cosA = (a * a + d * d - b * b) / (2 * a * d);
  const sinA = Math.sqrt(Math.max(0, 1 - cosA * cosA));
  _n.subVectors(pole, _S);
  _n.addScaledVector(dir, -_n.dot(dir));
  if (_n.lengthSq() < 1e-6) _n.set(0, 0, 1).addScaledVector(dir, -dir.z);
  _n.normalize();
  _E.copy(_S).addScaledVector(dir, a * cosA).addScaledVector(_n, a * sinA);
  _T.copy(_S).addScaledVector(dir, d);

  _X.crossVectors(dir, _n).normalize();
  _Y.subVectors(_S, _E).normalize();
  _Z.crossVectors(_X, _Y).normalize();
  _m4.makeBasis(_X, _Y, _Z);
  _qa.setFromRotationMatrix(_m4);
  _Y.subVectors(_E, _T).normalize();
  _Z.crossVectors(_X, _Y).normalize();
  _m4.makeBasis(_X, _Y, _Z);
  _qb.setFromRotationMatrix(_m4);

  upper.parent.getWorldQuaternion(_qp);
  upper.quaternion.copy(_qp.clone().invert().multiply(_qa));
  lower.quaternion.copy(_qa.clone().invert().multiply(_qb));
  upper.updateMatrixWorld(true);
}
