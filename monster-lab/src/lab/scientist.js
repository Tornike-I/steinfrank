// Dr. Frankenstein: a hunched puppet with a huge egg head, a brass loupe, a
// hunchback, a stained coat and enormous rubber gloves.
//
// Animation model: actions output a Pose (root transform, spine, look target,
// world-space wrist targets + hand orientations, feet targets). Poses blend on
// action change and are applied with 2-bone IK every frame. Hands are kept
// out of solid obstacles (the table, the patient's torso) and elbows are
// raised when an arm would pass through them.
import * as THREE from 'three';
import { clay, cloth, rubber, metal, glossy, glass } from '../three/materials.js';
import { textures } from '../three/textures.js';
import { lumpy, sausage, mesh, stitches, ellipsoidLine } from '../three/geom.js';

// Arms ignore his body and the props: the collision passes made the arms look
// tucked in or winged. Set to true to bring them back.
const ARM_COLLISIONS = false;

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
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

    // His own body as spheres riding on the skeleton, so arms and hands never
    // pass through it (torso, pot belly, hunch, coat skirt, head, chin).
    const sph = (obj, x, y, z, r) => ({ kind: 'sphere', obj, c: V(x, y, z), r, self: true });
    this.selfShapes = [
      sph(spine, 0, -0.14, 0, 0.25), // coat skirt over the hips
      sph(spine, 0, 0.07, 0, 0.21),
      sph(spine, 0, 0.25, 0, 0.22),
      sph(spine, 0, 0.42, 0, 0.155),
      sph(spine, 0, 0.14, 0.07, 0.19), // pot belly
      sph(spine, 0.03, 0.42, -0.1, 0.16), // hunch
      // The egg-shaped cranium as an ellipsoid fitted to its mesh (a single
      // ball was ~4 cm too wide at the lower sides, which kept the arms
      // needlessly far from his face), plus its flared crown, the brass
      // loupe and the big nose.
      { kind: 'ellipsoid', obj: this.cranium, rad: V(0.215, 0.262, 0.23), self: true },
      sph(head, 0, 0.40, -0.035, 0.13), // crown
      sph(head, 0.085, 0.29, 0.215, 0.075), // loupe
      sph(head, 0, 0.21, 0.27, 0.06), // nose
      sph(head, 0, 0.075, 0.09, 0.09), // jaw and chin
    ];
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
    const cranium = (this.cranium = mesh(g, skin));
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
    this._step++;
    const t = a.loop ? a.t : Math.min(a.t, a.duration);
    a.pose(t, this._raw);
    const w = smooth(t / (a.blend ?? 0.3));
    blendPose(this.pose, this._prev, this._raw, w);
    this.collide = a.collide !== false;
    this._dt = dt * this.timeScale;
    if (this._nudge) {
      const k = Math.exp(-dt * 1.5);
      this._nudge.R.multiplyScalar(k);
      this._nudge.L.multiplyScalar(k);
    }
    this.apply(this.pose);
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
    const jitter = () => 0;
    this.root.position.copy(p.rootPos);
    this.root.rotation.y = p.rootYaw;
    this.hips.position.set(p.hipsX, p.hipsY, 0);
    this.spine.rotation.set(p.spineX + jitter(1), p.spineY + jitter(2), p.spineZ + jitter(3));
    this.root.updateMatrixWorld(true);

    // Head look-at in the neck's parent (chest) space, clamped.
    const chestInv = new THREE.Matrix4().copy(this.chest.matrixWorld).invert();
    const lp = p.look.clone().applyMatrix4(chestInv).sub(this.neck.position);
    const yawT = THREE.MathUtils.clamp(Math.atan2(lp.x, lp.z), -1.1, 1.1);
    const pitchT = THREE.MathUtils.clamp(-Math.atan2(lp.y - 0.25, Math.hypot(lp.x, lp.z)), -0.6, 1.0);
    // Actions often switch what he looks at outright; the head follows
    // quickly but smoothly rather than snapping (which read as the eyes and
    // brows glitching).
    const dt = this._dt ?? 1 / 60;
    const g = (this._gaze ||= { yaw: yawT, pitch: pitchT });
    const kG = 1 - Math.exp(-dt * 9);
    g.yaw += (yawT - g.yaw) * kG;
    g.pitch += (pitchT - g.pitch) * kG;
    const yaw = g.yaw, pitch = g.pitch;
    this.neck.rotation.set(pitch * 0.4, yaw * 0.4, 0);
    this.head.rotation.set(pitch * 0.6 + jitter(4), yaw * 0.6, p.headTilt);
    // Actions switch brow and jaw between values outright; ease the face
    // toward them so expressions never snap (blinks stay quick).
    const f = (this._face ||= { brow: p.brow, jaw: p.jaw, blink: p.blink });
    const ease = (rate) => 1 - Math.exp(-dt * rate);
    f.brow += (p.brow - f.brow) * ease(10);
    f.jaw += (p.jaw - f.jaw) * ease(28);
    f.blink += (p.blink - f.blink) * ease(45);
    this.jaw.rotation.x = f.jaw * 0.55;
    this.mouth.scale.y = 0.026 + f.jaw * 0.05;
    for (const l of this.lids) l.lid.rotation.x = lerp(l.open, Math.PI / 2, f.blink);
    this.brows.forEach((b, i) => {
      b.b.position.y = b.y - f.brow * 0.025;
      b.b.rotation.z = Math.PI / 2 + b.rz + (i === 0 ? -1 : 1) * f.brow * 0.3;
    });
    this.root.updateMatrixWorld(true);

    if (!ARM_COLLISIONS) {
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
      this.root.updateMatrixWorld(true);
    } else {
      // Arms, with posture correction: if an arm would pass through his own
      // head or chin (he leans over the slab with a very big head), he lifts his
      // head back and, if that is not enough, straightens up a little.
      // He straightens his back a little first, then alternates tipping his
      // head back with straightening further. The correction eases in within a
      // few frames and lets go slowly, so his head never jerks or bobs from
      // frame to frame (the face glitch) when an arm passes near it.
      const bend = (i, frac) => {
        if ((i < 2 || i % 2 === 1) && this.spine.rotation.x > 0.05) {
          this.spine.rotation.x -= 0.07 * frac;
        } else {
          this.neck.rotation.x -= 0.1 * frac;
          this.head.rotation.x -= 0.1 * frac;
        }
      };
      const neckX = this.neck.rotation.x, headX = this.head.rotation.x;
      this._solveArms(p);
      // Each step re-solves only the arm that hits; the other is re-solved once
      // at the end (its shoulder moved with the bend).
      let need = 0, hitters;
      const stale = new Set();
      for (; need < 10 && (hitters = this._headHitters()).length; need++) {
        bend(need, 1);
        this.root.updateMatrixWorld(true);
        for (const k of ['R', 'L']) {
          if (hitters.includes(k)) { this._solveArm(k, p, this._nudge?.[k]); stale.delete(k); } else stale.add(k);
        }
      }
      for (const k of stale) this._solveArm(k, p, this._nudge?.[k]);
      const prev = this._lean || 0;
      const held = need > prev ? Math.min(need, prev + dt * 90) : Math.max(need, prev - dt * 2.5);
      this._lean = held;
      if (Math.abs(held - need) > 1e-3) {
        // Redo the posture with the eased amount instead of the raw one.
        this.spine.rotation.x = p.spineX;
        this.neck.rotation.x = neckX;
        this.head.rotation.x = headX;
        for (let i = 0; i < held; i++) bend(i, Math.min(1, held - i));
        this.root.updateMatrixWorld(true);
        this._solveArms(p);
      }
      // Then the real glove geometry (thumb and fingertips included) is kept
      // out of solids, out of himself, and out of the other hand. Moving the
      // hands re-solves the arms, which can bring an elbow back into his head,
      // so check that once more.
      this._settleHands(p);
      if (this._armsHitHead()) {
        // A couple more steps, kept only if they actually clear it (with his
        // arms up behind his head, tipping it back would only make it worse).
        const keep = [this.spine.rotation.x, this.neck.rotation.x, this.head.rotation.x];
        let cleared = false;
        for (let i = Math.ceil(held); i < Math.ceil(held) + 3 && !cleared; i++) {
          bend(i, 1);
          this.root.updateMatrixWorld(true);
          this._solveArms(p);
          cleared = !this._armsHitHead();
        }
        if (!cleared) {
          [this.spine.rotation.x, this.neck.rotation.x, this.head.rotation.x] = keep;
          this.root.updateMatrixWorld(true);
          this._solveArms(p);
        }
      }
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

  _solveArms(p) {
    for (const k of ['R', 'L']) this._solveArm(k, p, this._nudge?.[k]);
  }

  // `nudge` shifts the hand from its posed target (see _settleHands).
  _solveArm(k, p, nudge = null) {
    const arm = this.arms[k];
    const h = p['h' + k];
    const target = h.p.clone();
    if (nudge) target.add(nudge);
    // Keep the hand (and its fingertips) out of solids and out of himself.
    this._keepOut(target, 0.06, h.q);
    // His body doesn't move while one arm is solved: place the shapes once.
    const snap = this._shapeSnapshot();
    // Elbow placements to try, in order: hanging out and back, out to the
    // side, lifted up and out (reaching over), forward and out.
    const poles = [
      this.local(p.rootPos, p.rootYaw, arm.side * 0.9, 0.9, -0.6),
      this.local(p.rootPos, p.rootYaw, arm.side * 1.6, 0.9, -0.1),
      this.local(p.rootPos, p.rootYaw, arm.side * 0.8, 2.0, -0.2),
      this.local(p.rootPos, p.rootYaw, arm.side * 1.7, 1.5, 0.1),
      this.local(p.rootPos, p.rootYaw, arm.side * 1.3, 1.1, 0.6),
    ];
    let clear = false;
    for (let i = 0; i < poles.length && !clear; i++) {
      solveTwoBone(arm.upper, arm.fore, UPPER, FORE, target, poles[i]);
      clear = !this._armHits(arm, target, snap);
    }
    // Only his head in the way? Then keep the hand where it should be: the
    // head-lift in apply() deals with that, more cheaply and without pulling
    // the hand off its mark (a scalpel off the incision line).
    const head = (o) => o.src.obj === this.head || o.src.obj === this.cranium;
    if (!clear) {
      const body = snap.filter((o) => !head(o));
      for (let i = 0; i < poles.length && !clear; i++) {
        solveTwoBone(arm.upper, arm.fore, UPPER, FORE, target, poles[i]);
        clear = !this._armHits(arm, target, body);
      }
    }
    if (!clear) {
      // Last resort: move the hand outward from his chest (and up, over any
      // table or patient) until the whole arm is clear.
      const chest = this.chest.getWorldPosition(V());
      const out = target.clone().sub(chest).setY(0);
      if (out.lengthSq() < 1e-4) out.copy(this.dir(p.rootYaw, arm.side, 0, 0.5));
      out.normalize();
      let best = poles[1];
      for (let i = 0; i < 10 && !clear; i++) {
        target.addScaledVector(out, 0.035);
        target.y += 0.03; // a little higher too: forearms come down onto things more steeply
        for (const pole of [poles[1], poles[3], poles[2]]) {
          solveTwoBone(arm.upper, arm.fore, UPPER, FORE, target, pole);
          if (!this._armHits(arm, target, snap)) { clear = true; best = pole; break; }
        }
      }
      if (!clear) solveTwoBone(arm.upper, arm.fore, UPPER, FORE, target, best);
    }
    setWorldQuat(arm.hand, h.q, arm.fore);
    const c = h.curl;
    arm.fingers.forEach((f, i) => { f.rotation.x = -c * (i % 2 ? 1.25 : 1.0); });
    arm.fingers.thumb.rotation.x = 0.4 + c * 0.5;
    arm.hand.updateMatrixWorld(true);
  }

  // Points (with radii) covering a glove as it is now posed: a 3×3 grid over
  // the palm, two along each finger segment, two along the thumb.
  _handPoints(k) {
    const arm = this.arms[k];
    const g = this._gloveGeo(arm);
    const out = [];
    for (const x of [-0.55, 0, 0.55]) {
      for (const y of [-0.55, 0, 0.55]) out.push({ p: g.palm.localToWorld(V(x, y, 0)), r: x || y ? 0.03 : 0.033 });
    }
    arm.fingers.forEach((f, i) => {
      const m = g.fingerMesh[i], r = i % 2 ? 0.014 : 0.0155;
      for (const u of [0.35, 0.9]) out.push({ p: m.localToWorld(V(0, g.fingerLen[i] * u, 0)), r });
    });
    for (const u of [0.35, 0.9]) out.push({ p: g.thumbMesh.localToWorld(V(0, g.thumbLen * u, 0)), r: 0.016 });
    return out;
  }

  _gloveGeo(arm) {
    if (arm._glove) return arm._glove;
    const meshOf = (o) => o.children.find((c) => c.isMesh);
    const lenOf = (m) => { m.geometry.computeBoundingBox(); return m.geometry.boundingBox.min.y; };
    const fingerMesh = arm.fingers.map(meshOf);
    const thumbMesh = meshOf(arm.fingers.thumb);
    arm._glove = {
      palm: arm.hand.children.find((c) => c.isMesh),
      fingerMesh, fingerLen: fingerMesh.map(lenOf),
      thumbMesh, thumbLen: lenOf(thumbMesh),
    };
    return arm._glove;
  }

  // After IK: nudge each hand until no part of either glove is inside a
  // solid (or his own body), and the two gloves only touch, never overlap.
  // A hand holding a tool stays put if it can; the free one gives way.
  // The nudge carries over to the next frame (fading slowly), so hands that
  // stay in contact, like rubbing palms, rarely need a re-solve.
  _settleHands(p) {
    const nudge = (this._nudge ||= { R: V(), L: V() });
    const holding = (k) => this.arms[k].grip.children.length > 0;
    for (let iter = 0; iter < 6; iter++) {
      const pts = { R: this._handPoints('R'), L: this._handPoints('L') };
      const shapes = this._shapeSnapshot();
      const fix = { R: V(), L: V() };
      let any = false;
      for (const k of ['R', 'L']) {
        // Broad phase: the whole glove fits in a 0.16 m ball round the palm.
        const c = pts[k][4].p;
        for (const o of shapes) {
          if (o.ball && c.distanceTo(o.ball) > o.r + 0.2) continue;
          for (const q of pts[k]) {
            const m = o.src.self ? q.r * 0.5 : q.r;
            if (!this._hitFast(o, q.p, m)) continue;
            const e = this._exit(o.src, q.p, m);
            if (e.lengthSq() > fix[k].lengthSq()) fix[k].copy(e);
            any = true;
          }
        }
      }
      // Glove against glove: the deepest overlap, resolved by moving the
      // hands apart as wholes (centre to centre). Pushing along each
      // overlapping pair instead fights itself when the fingers interlock.
      let worst = 0;
      if (pts.R[4].p.distanceTo(pts.L[4].p) < 0.4) {
        for (const a of pts.R) {
          for (const b of pts.L) worst = Math.max(worst, a.r + b.r - a.p.distanceTo(b.p));
        }
      }
      if (worst > 0.002) {
        const centre = (list) => list.reduce((acc, q) => acc.add(q.p), V()).multiplyScalar(1 / list.length);
        let dir = centre(pts.R).sub(centre(pts.L));
        if (dir.lengthSq() < 1e-8) dir = this.dir(p.rootYaw, -1, 0, 0);
        dir.normalize().multiplyScalar(worst + 0.003);
        const kR = holding('R') === holding('L') ? 0.5 : holding('R') ? 0 : 1;
        fix.R.addScaledVector(dir, kR);
        fix.L.addScaledVector(dir, -(1 - kR));
        any = true;
      }
      if (!any) return;
      for (const k of ['R', 'L']) {
        if (fix[k].lengthSq() < 1e-10) continue;
        nudge[k].add(fix[k]);
        this._solveArm(k, p, nudge[k]);
      }
    }
  }

  // Shapes with their world centres worked out once (spheres), for many
  // point tests in a row.
  _shapeSnapshot(list = this._shapes()) {
    return list.map((o) => {
      if (o.kind === 'sphere') { const c = o.obj.localToWorld(o.c.clone()); return { src: o, ball: c, r: o.r }; }
      if (o.kind === 'ellipsoid') {
        const s = o.obj.getWorldScale(V()).x || 1;
        return { src: o, ball: o.obj.getWorldPosition(V()), r: Math.max(o.rad.x, o.rad.y, o.rad.z) * s, inv: o.obj.matrixWorld.clone().invert(), s };
      }
      return { src: o, ball: null };
    });
  }

  _hitFast(o, p, margin) {
    const k = o.src.kind;
    if (k === 'sphere') return p.distanceTo(o.ball) < o.r + margin;
    if (k === 'ellipsoid') {
      if (p.distanceTo(o.ball) > o.r + margin) return false;
      const l = _L.copy(p).applyMatrix4(o.inv), m = margin / o.s, r = o.src.rad;
      return (l.x / (r.x + m)) ** 2 + (l.y / (r.y + m)) ** 2 + (l.z / (r.z + m)) ** 2 < 1;
    }
    return this._hit(o.src, p, margin);
  }


  _armsHitHead() { return this._headHitters().length > 0; }

  // Which arms ('R', 'L') pass through his head or chin.
  _headHitters() {
    const head = this._shapeSnapshot(this.selfShapes.filter((o) => o.obj === this.head || o.obj === this.cranium));
    const out = [];
    for (const k of ['R', 'L']) {
      const arm = this.arms[k];
      const s0 = arm.upper.getWorldPosition(V()), e = arm.fore.getWorldPosition(V()), w = arm.hand.getWorldPosition(V());
      const hits = () => {
        for (let i = 1; i <= 8; i++) {
          for (const q of [s0.clone().lerp(e, i / 8), e.clone().lerp(w, i / 8)]) {
            for (const o of head) if (this._hitFast(o, q, 0.02)) return true;
          }
        }
        return false;
      };
      if (hits()) out.push(k);
    }
    return out;
  }

  gripWorld(key) { return this.arms[key].grip.getWorldPosition(V()); }

  // ------------------------------------------------------------ collision
  // External obstacles (set by the Director):
  //   { kind: 'box', min, max } and { kind: 'ellipsoid', obj, rad (local radii) }
  // plus his own body (this.selfShapes, spheres on bones), which always applies.
  // `external` false checks only the external obstacles; actions with
  // `collide: false` (reaching into a wound) skip external ones only.
  _shapes(external = true) {
    const list = [...this.selfShapes];
    if (external && this.collide) list.push(...(this.obstacles || []));
    return list;
  }

  _hit(o, p, margin) {
    if (o.kind === 'box') {
      return p.x > o.min.x - margin && p.x < o.max.x + margin && p.z > o.min.z - margin && p.z < o.max.z + margin && p.y < o.max.y + margin && p.y > o.min.y;
    }
    if (o.kind === 'sphere') {
      return p.distanceTo(o.obj.localToWorld(o.c.clone())) < o.r + margin;
    }
    const l = o.obj.worldToLocal(p.clone());
    const m = margin / (o.obj.getWorldScale(V()).x || 1);
    return (l.x / (o.rad.x + m)) ** 2 + (l.y / (o.rad.y + m)) ** 2 + (l.z / (o.rad.z + m)) ** 2 < 1;
  }

  _inside(p, margin, external = true) {
    for (const o of this._shapes(external)) if (this._hit(o, p, margin)) return o;
    return null;
  }

  // The smallest move that takes p out of shape o (plus margin).
  _exit(o, p, margin) {
    if (o.kind === 'box') return V(0, o.max.y + margin - p.y, 0);
    if (o.kind === 'sphere') {
      const c = o.obj.localToWorld(o.c.clone());
      const d = p.clone().sub(c);
      const len = d.length() || 1;
      return d.multiplyScalar((o.r + margin) * 1.02 / len - 1);
    }
    const l = o.obj.worldToLocal(p.clone());
    const m = margin / (o.obj.getWorldScale(V()).x || 1);
    const r = V(o.rad.x + m, o.rad.y + m, o.rad.z + m);
    const n = V(l.x / r.x, l.y / r.y, l.z / r.z);
    n.multiplyScalar(1.02 / (n.length() || 1));
    return o.obj.localToWorld(V(n.x * r.x, n.y * r.y, n.z * r.z)).sub(p);
  }

  // Push a hand target (and its fingertips, given the hand orientation) out
  // of every shape it is inside.
  _keepOut(p, margin, q = null) {
    const tipOff = q ? V(0, -0.15, 0.02).applyQuaternion(q) : null;
    for (let iter = 0; iter < 5; iter++) {
      let moved = false;
      for (const o of this._shapes()) {
        if (this._hit(o, p, margin)) { p.add(this._exit(o, p, margin)); moved = true; }
        if (tipOff) {
          const tip = p.clone().add(tipOff);
          if (this._hit(o, tip, margin * 0.6)) { p.add(this._exit(o, tip, margin * 0.6)); moved = true; }
        }
      }
      if (!moved) return;
    }
  }

  // Does the upper arm or forearm pass through anything (himself included)?
  // The first stretch of the upper arm starts inside his shoulder, so skip it.
  _armHits(arm, wrist, snap = this._shapeSnapshot()) {
    const s = arm.upper.getWorldPosition(V());
    const e = arm.fore.getWorldPosition(V());
    const inside = (p, m) => snap.some((o) => this._hitFast(o, p, m));
    const q = V();
    for (let i = 1; i <= 8; i++) {
      const u = i / 8;
      if (u > 0.3 && inside(q.copy(s).lerp(e, u), 0.035)) return true;
      if (i < 8 && inside(q.copy(e).lerp(wrist, u), 0.03)) return true;
    }
    return false;
  }
}

// ------------------------------------------------------------------ IK ----
const _L = V();
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
