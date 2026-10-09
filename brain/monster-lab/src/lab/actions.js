// Scientist choreography. Each factory returns an action for Scientist.play():
// { name, duration, loop?, blend?, pose(t, pose), events? }.
// Pose functions are evaluated every frame and may move
// props (limbs, the patient, tools) so everything ticks in the same rhythm.
import * as THREE from 'three';
import { handQuat, smooth, seg } from './scientist.js';
import { SPOTS } from './constants.js';
import * as P from './props.js';
import { setPallor } from '../monsters/monsterGen.js';
import { mesh } from '../three/geom.js';
import { rubber } from '../three/materials.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const GRIP = V(0, -0.085, 0.035);
const UP = V(0, 1, 0);

export function makeActions(lab, sfx) {
  const sci = lab.scientist;
  const tools = { scalpel: P.scalpel(), needle: P.needle(), syringe: P.syringe(), nozzle: P.nozzle() };
  const cam = () => lab.camera.position.clone();

  function equip(key, tool) {
    const cur = sci.held[key];
    if (cur === tool) return;
    if (cur) { cur.parent?.remove(cur); sci.held[key] = null; }
    if (tool) sci.hold(key, tool);
  }
  const empty = () => { equip('R', null); equip('L', null); };

  // Wrist position so that a tool tip (grip-local) lands on `tip`.
  function wristFor(tip, q, tipLocal = V()) {
    return tip.clone().sub(GRIP.clone().add(tipLocal).applyQuaternion(q));
  }

  const cutQ = (key, lean = 0.45) => handQuat(V(0, -1, lean), V(key === 'R' ? 1 : -1, 0, 0));
  const pressQ = () => handQuat(V(0.1, -0.5, 1), V(0, -1, 0));
  const workRoot = (x) => V(THREE.MathUtils.clamp(x, 0.95, 2.05), 0, SPOTS.work.z);

  function workBase(p, site, lean = 0.34) {
    sci.stand(p, workRoot(site.x), 0, site);
    p.spineX = lean;
    p.hipsY -= 0.04;
    p.brow = 0.8;
    p.blink = 0;
    return p;
  }

  let lastBlood = 0;
  function bleed(pos, n = 3, dir = UP) {
    lastBlood++;
    lab.fx.blood(pos, dir, n, 0.9, 1.2);
  }

  // ---------------------------------------------------------------- idle --
  const idle = () => ({
    name: 'idle', loop: true, blend: 0.5,
    pose(t, p) {
      const look = cam();
      const g = seg(t % 11, 6, 6.4) * (1 - seg(t % 11, 7.4, 7.9));
      look.lerp(V(-1.4, 1.8, -1.2), g);
      sci.stand(p, SPOTS.idle, 0, look);
      const chuckle = (t % 9) > 5.6 && (t % 9) < 6.6;
      const c = t * (chuckle ? 11 : 5);
      const L = (x, y, z) => sci.local(SPOTS.idle, 0, x, y, z);
      p.hR.p.copy(L(-0.06 + Math.cos(c) * 0.025, 1.02 + Math.sin(c) * 0.02, 0.34));
      p.hL.p.copy(L(0.06 - Math.cos(c) * 0.025, 1.02 - Math.sin(c) * 0.02, 0.34));
      handQuat(V(0, 0.55, 1), V(1, 0, 0.2), p.hR.q);
      handQuat(V(0, 0.55, 1), V(-1, 0, 0.2), p.hL.q);
      p.hR.curl = p.hL.curl = 0.45;
      p.spineX = 0.24 + Math.sin(t * 1.7) * 0.025;
      p.hipsY += Math.sin(t * 1.7) * 0.006;
      p.headTilt = Math.sin(t * 0.45) * 0.14;
      p.blink = (t % 3.7) < 0.13 ? 1 : 0;
      p.jaw = chuckle ? 0.25 + Math.abs(Math.sin(t * 18)) * 0.35 : 0.08;
      p.brow = 0.35 + (chuckle ? 0.4 : 0);
    },
  });

  const greet = () => ({
    name: 'greet', duration: 2.2, blend: 0.35,
    pose(t, p) {
      sci.stand(p, SPOTS.idle, 0, cam());
      const L = (x, y, z) => sci.local(SPOTS.idle, 0, x, y, z);
      const up = seg(t, 0, 0.35) * (1 - seg(t, 1.8, 2.2));
      const wave = Math.sin(t * 11) * 0.09;
      p.hR.p.lerp(L(-0.33 + wave, 1.72, 0.18), up);
      handQuat(V(wave * 2, 1, 0.15), V(0, 0, 1), p.hR.q);
      p.hR.curl = 0.1;
      p.hL.p.copy(L(0.1, 0.98, 0.3));
      handQuat(V(-0.6, -0.4, 0.4), V(-0.3, 0, -1), p.hL.q);
      p.jaw = 0.35 + Math.abs(Math.sin(t * 7)) * 0.2 * up;
      p.headTilt = -0.15 * up;
      p.brow = 0.2;
    },
  });

  const celebrate = (dur = 1.6, at = SPOTS.work) => ({
    name: 'celebrate', duration: dur, blend: 0.25,
    pose(t, p) {
      const look = cam().lerp(V(at.x, 2.4, 0), 0.4);
      sci.stand(p, at, 0, look);
      const L = (x, y, z) => sci.local(at, 0, x, y, z);
      const sh = Math.sin(t * 16) * 0.04;
      p.hR.p.copy(L(-0.38, 1.85 + sh, 0.12));
      p.hL.p.copy(L(0.38, 1.85 - sh, 0.12));
      handQuat(V(-0.3, 1, 0), V(0, 0, 1), p.hR.q);
      handQuat(V(0.3, 1, 0), V(0, 0, 1), p.hL.q);
      p.hR.curl = p.hL.curl = 0.1;
      p.spineX = 0.0;
      p.jaw = 0.4 + Math.abs(Math.sin(t * 20)) * 0.5;
      p.headTilt = Math.sin(t * 9) * 0.1;
      p.brow = -0.5;
    },
  });

  // Shuffle from A to B with planted, alternating feet.
  const walk = (from, to, yawEnd = 0, speedK = 1) => {
    const dist = from.distanceTo(to);
    const dur = Math.max(0.5, dist / (1.6 * speedK));
    const N = Math.max(2, Math.round(dist / 0.24) * 2 / 2) | 0;
    const travel = Math.atan2(to.x - from.x, to.z - from.z);
    return {
      name: 'walk', duration: dur, blend: 0.2,
      pose(t, p) {
        const u = smooth(t / dur);
        const root = from.clone().lerp(to, u);
        let yaw = travel;
        if (dist < 0.05) yaw = yawEnd;
        else if (u < 0.15) yaw = lerpAngle(0, travel, u / 0.15);
        else if (u > 0.8) yaw = lerpAngle(travel, yawEnd, (u - 0.8) / 0.2);
        sci.stand(p, root, yaw, cam());
        const right = sci.dir(yaw, 1, 0, 0);
        const stepFn = (x) => {
          const k = Math.floor(x), f = x - k;
          return k + smooth((f - 0.5) * 2);
        };
        const lift = (x) => Math.sin(Math.PI * Math.min(1, Math.max(0, ((x % 1) - 0.5) * 2))) * 0.08;
        const xr = (t / dur) * N / 2, xl = xr + 0.5;
        const qr = Math.min(1, stepFn(xr) / (N / 2)), ql = Math.min(1, stepFn(xl) / (N / 2));
        p.fR.copy(from.clone().lerp(to, qr)).addScaledVector(right, -0.12).setY(0.07 + (qr < 1 ? lift(xr) : 0));
        p.fL.copy(from.clone().lerp(to, ql)).addScaledVector(right, 0.12).setY(0.07 + (ql < 1 ? lift(xl) : 0));
        const sw = Math.sin((t / dur) * N * Math.PI);
        p.hipsY -= Math.abs(sw) * 0.03;
        p.spineZ = sw * 0.06;
        const L = (x, y, z) => sci.local(root, yaw, x, y, z);
        p.hR.p.copy(L(-0.24, 0.9, 0.12 + sw * 0.12));
        p.hL.p.copy(L(0.24, 0.9, 0.12 - sw * 0.12));
        p.spineX = 0.32;
      },
    };
  };

  const snap = () => ({
    name: 'snap', duration: 0.85, blend: 0.2,
    events: [{ t: 0.45, fn: () => sfx.play('snap') }],
    pose(t, p) {
      const L = (x, y, z) => sci.local(SPOTS.idle, 0, x, y, z);
      sci.stand(p, SPOTS.idle, 0, L(0, 1.0, 0.5));
      p.hR.p.copy(L(-0.08, 1.05, 0.36));
      handQuat(V(0.2, 0.5, 1), V(0, 1, 0), p.hR.q);
      p.hR.curl = 0.2;
      const pull = seg(t, 0.1, 0.42) * (1 - seg(t, 0.46, 0.7));
      p.hL.p.copy(L(-0.02, 1.0, 0.27)).add(V(0.1 * pull, -0.02 * pull, -0.08 * pull));
      handQuat(V(-1, 0, 0.3), V(0, 0, -1), p.hL.q);
      p.hL.curl = 0.8;
      p.jaw = t > 0.45 ? 0.5 : 0.1;
      p.brow = t > 0.45 ? -0.3 : 0.6;
    },
  });

  // ------------------------------------------------------------ surgery --
  const incise = (patient) => {
    const w = patient.planIncision();
    const n = w.normalAt(0.5);
    const q = cutQ('R');
    const tip = tools.scalpel.userData.tip;
    let sounded = false;
    return {
      name: 'incise', duration: 1.9, blend: 0.3,
      site: () => w.pointAt(Math.min(1, Math.max(0, w.cut))),
      wound: w,
      events: [{ t: 0.0, fn: () => { equip('R', tools.scalpel); equip('L', null); } }],
      pose(t, p) {
        const u = seg(t, 0.55, 1.5);
        const tipPos = w.pointAt(u);
        workBase(p, tipPos);
        const hover = 1 - seg(t, 0.35, 0.55) + seg(t, 1.5, 1.8);
        const tipT = tipPos.clone().addScaledVector(n, hover * 0.12 - 0.004);
        p.hR.p.copy(wristFor(tipT, q, tip));
        p.hR.q.copy(q);
        p.hR.curl = 0.85;
        const press = w.pointAt(0.35).addScaledVector(n, 0.07).add(V(0, 0, -0.13));
        p.hL.p.copy(press);
        p.hL.q.copy(pressQ());
        p.hL.curl = 0.15;
        p.jaw = 0.15 + (t > 0.55 && t < 1.5 ? 0.15 : 0);
        p.look.copy(tipPos);
        if (t > 0.55 && t < 1.5) {
          w.setCut(u);
          bleed(tipPos, 2);
          if (!sounded) { sounded = true; sfx.play('slice'); }
        }
        if (t > 1.5) w.setOpen(seg(t, 1.5, 1.9));
      },
    };
  };

  const sew = (patient) => {
    const w = patient.openWound() || patient.anyWound();
    const startStitch = w.stitchesDone;
    const per = 0.62, count = 4;
    const q = cutQ('R', 0.2);
    const tip = tools.needle.userData.tip;
    const threadMat = new THREE.LineBasicMaterial({ color: 0x1b140f });
    const threadGeo = new THREE.BufferGeometry().setFromPoints([V(), V()]);
    const thread = new THREE.Line(threadGeo, threadMat);
    const events = [{ t: 0, fn: () => { equip('R', tools.needle); equip('L', null); lab.scene.add(thread); } }];
    for (let i = 0; i < count; i++) events.push({ t: 0.25 + i * per + 0.28, fn: () => { if (w.addStitch()) sfx.play('stitch'); } });
    events.push({ t: 0.25 + count * per + 0.2, fn: () => lab.scene.remove(thread) });
    return {
      name: 'sew', duration: 0.25 + count * per + 0.3, blend: 0.3,
      site: () => w.mid,
      wound: w,
      pose(t, p) {
        const i = Math.min(count - 1, Math.max(0, Math.floor((t - 0.25) / per)));
        const c = ((t - 0.25) - i * per) / per;
        const total = w.local.length;
        const idx = Math.min(total - 1, startStitch + i);
        const pt = w.stitchPoint(idx);
        const n = w.normalAt(idx / (total - 1));
        workBase(p, pt);
        const down = t < 0.25 ? 0.6 : seg(c, 0, 0.35) * (1 - seg(c, 0.45, 0.85));
        const tipT = pt.clone().addScaledVector(n, (1 - down) * 0.3 - 0.01).add(V(0.0, 0, -(1 - down) * 0.12));
        p.hR.p.copy(wristFor(tipT, q, tip));
        p.hR.q.copy(q);
        p.hR.curl = 0.85;
        p.hL.p.copy(w.mid.addScaledVector(n, 0.07).add(V(0.12, 0, -0.12)));
        p.hL.q.copy(pressQ());
        p.hL.curl = 0.3;
        p.look.copy(pt).addScaledVector(n, 0.1);
        p.jaw = 0.1 + down * 0.15;
        // Thread from the needle back to the stitch.
        const np = sci.arms.R.grip.localToWorld(tip.clone());
        threadGeo.setFromPoints([np, pt]);
        if (down > 0.7) bleed(pt, 1);
      },
    };
  };

  const attach = (patient) => {
    const d = patient.nextDetached();
    const startPos = d.obj.position.clone();
    const startQuat = d.obj.quaternion.clone();
    const sock = patient.socketWorld(d);
    const axis = () => V(0, -1, 0).applyQuaternion(d.obj.quaternion);
    let attached = false;
    return {
      name: 'attach', duration: 2.9, blend: 0.3,
      // Frame where the limb is going (the socket), with a bit of where it came from.
      site: () => d.obj.position.clone().lerp(sock.pos, 0.7).add(V(0, 0.1, 0)),
      events: [
        { t: 0, fn: () => empty() },
        { t: 0.5, fn: () => sfx.play('squelch') },
        { t: 2.0, fn: () => { attached = true; patient.reattach(d); sfx.play('pop'); bleed(sock.pos, 14, UP); } },
        { t: 2.3, fn: () => sfx.play('stitch') },
        { t: 2.55, fn: () => sfx.play('stitch') },
      ],
      pose(t, p) {
        const carry = seg(t, 0.5, 1.5);
        if (!attached) {
          const arc = Math.sin(carry * Math.PI) * 0.28;
          d.obj.position.copy(startPos).lerp(sock.pos, carry).add(V(0, arc, 0));
          d.obj.quaternion.slerpQuaternions(startQuat, sock.quat, carry);
          if (t > 1.5 && t < 2.0) {
            const wig = Math.sin(t * 40) * 0.15 * (1 - seg(t, 1.9, 2.0));
            d.obj.quaternion.copy(sock.quat).multiply(new THREE.Quaternion().setFromAxisAngle(V(0, 0, 1), wig));
          }
          d.obj.updateMatrixWorld(true);
        }
        const limbPos = attached ? sock.pos.clone() : d.obj.position.clone();
        const a = attached ? V(0, -1, 0).applyQuaternion(sock.quat) : axis();
        workBase(p, limbPos, 0.5);
        const hold1 = limbPos.clone().addScaledVector(a, 0.06).add(V(0, 0.08, 0));
        const hold2 = limbPos.clone().addScaledVector(a, 0.2).add(V(0, 0.08, 0));
        const reach = seg(t, 0, 0.5);
        p.hR.p.lerp(hold1, reach);
        p.hL.p.lerp(hold2, reach);
        p.hR.q.copy(handQuat(V(0, -1, 0.3), V(1, 0, 0)));
        p.hL.q.copy(handQuat(V(0, -1, 0.3), V(-1, 0, 0)));
        p.hR.curl = p.hL.curl = t > 0.45 && t < 2.0 ? 0.9 : 0.3;
        if (t > 2.0) {
          const bob = Math.abs(Math.sin(t * 14)) * 0.08;
          p.hR.p.copy(sock.pos).add(V(-0.04, 0.12 + bob, -0.1));
          p.hL.p.copy(sock.pos).add(V(0.1, 0.08, -0.12));
        }
        p.look.copy(limbPos);
        p.jaw = t > 1.5 && t < 2.1 ? 0.35 : 0.1;
        p.brow = 1;
      },
    };
  };

  const organ = (patient) => {
    let w = patient.openWound() || patient.anyWound();
    const ropeMat = patient.wounds[0]?.organs.children[0]?.material;
    let rope = null, chunk = null;
    const n = w.normalAt(0.5);
    const mid = w.mid;
    let ropeEnd = mid.clone();
    const fallbackRope = rubber(0xd9707a);
    return {
      name: 'organ', duration: 2.7, blend: 0.3, collide: false, // hands go into the wound on purpose
      site: () => mid.clone().lerp(ropeEnd, 0.5),
      events: [
        { t: 0, fn: () => { empty(); if (w.open < 0.8) w.setOpen(1); } },
        { t: 0.45, fn: () => { sfx.play('squelch'); bleed(mid, 16); } },
        { t: 1.0, fn: () => sfx.play('squelch') },
        {
          t: 1.85, fn: () => {
            if (rope) { lab.scene.remove(rope); rope.geometry.dispose(); rope = null; }
            chunk = P.gutChunk();
            chunk.position.copy(ropeEnd);
            lab.scene.add(chunk);
            lab.toss(chunk, V(-1.6, 2.2, -1.2), () => { sfx.play('splat'); bleed(chunk.position, 8); });
            sfx.play('laugh');
          },
        },
      ],
      pose(t, p) {
        workBase(p, mid, 0.45);
        const dive = seg(t, 0, 0.45);
        const pull = seg(t, 0.5, 1.4);
        const wind = seg(t, 1.4, 1.8);
        const off = w.dir.multiplyScalar(0.06);
        const inside = mid.clone().addScaledVector(n, 0.07);
        const high = mid.clone().addScaledVector(n, 0.45).add(V(-0.1, 0, -0.15));
        const over = sci.local(workRoot(mid.x), 0, -0.42, 1.72, -0.05); // wind up above the shoulder, not behind the hunch
        const hand = inside.clone().lerp(high, pull).lerp(over, wind);
        p.hR.p.copy(hand).sub(off);
        p.hL.p.copy(hand).add(off).add(V(0, -0.04 * pull, 0));
        // Only the right hand winds up over the shoulder; the left lets go and
        // stays in front instead of reaching across his body.
        if (wind > 0) p.hL.p.lerp(sci.local(workRoot(mid.x), 0, 0.24, 1.0, 0.32), wind);
        if (t < 0.5) { p.hR.p.lerp(inside, dive); p.hL.p.lerp(inside, dive); }
        p.hR.q.copy(handQuat(V(0, -1, 0.2), V(1, 0, 0)));
        p.hL.q.copy(handQuat(V(0, -1, 0.2), V(-1, 0, 0)));
        p.hR.curl = p.hL.curl = t > 0.45 ? 0.95 : 0.2;
        ropeEnd = hand.clone().add(V(0, -0.1, 0));
        if (t > 0.5 && t < 1.85) {
          if (rope) { lab.scene.remove(rope); rope.geometry.dispose(); }
          const ctrl = mid.clone().lerp(ropeEnd, 0.5).add(V(0.08, -0.05, 0.05));
          const curve = new THREE.QuadraticBezierCurve3(mid.clone(), ctrl, ropeEnd);
          rope = mesh(new THREE.TubeGeometry(curve, 16, 0.025, 7), ropeMat || fallbackRope);
          lab.scene.add(rope);
          if (Math.random() < 0.5) bleed(ropeEnd, 2, V(0, -1, 0));
        }
        p.look.copy(t < 1.4 ? hand : mid);
        p.spineY = -0.4 * wind;
        p.jaw = 0.2 + pull * 0.4 + (t > 1.85 ? Math.abs(Math.sin(t * 18)) * 0.4 : 0);
        if (t > 1.85) {
          // wipe hands on apron
          const wipe = Math.sin(t * 16) * 0.05;
          const L = (x, y, z) => sci.local(workRoot(mid.x), 0, x, y, z);
          p.hR.p.copy(L(-0.1, 0.95 + wipe, 0.3));
          p.hL.p.copy(L(0.1, 0.95 - wipe, 0.3));
          p.hR.q.copy(handQuat(V(0, -1, 0), V(0, 0, -1)));
          p.hL.q.copy(handQuat(V(0, -1, 0), V(0, 0, -1)));
          p.spineX = 0.3;
          p.look.copy(cam());
          p.brow = -0.3;
        }
      },
    };
  };

  const inject = (patient) => {
    const m = patient.m;
    m.torso.updateMatrixWorld(true);
    const site = m.torso.localToWorld(V(0, m.torsoRadii.y * 0.7, m.torsoRadii.z * 0.75));
    const q = handQuat(V(0.2, -1, 0.15), V(1, 0, 0));
    const tip = tools.syringe.userData.tip;
    return {
      name: 'inject', duration: 2.3, blend: 0.3,
      site: () => site.clone(),
      events: [
        { t: 0, fn: () => { equip('R', tools.syringe); equip('L', null); tools.syringe.userData.juice.scale.y = 1; } },
        { t: 0.6, fn: () => { sfx.play('squelch'); bleed(site, 4); } },
        { t: 0.75, fn: () => sfx.play('gurgle') },
      ],
      pose(t, p) {
        workBase(p, site);
        const insert = seg(t, 0.4, 0.62) * (1 - seg(t, 1.6, 1.85));
        const tipT = site.clone().add(V(0, 0.18 * (1 - insert) - 0.02, -0.05 * (1 - insert)));
        p.hR.p.copy(wristFor(tipT, q, tip));
        p.hR.q.copy(q);
        p.hR.curl = 0.85;
        const push = seg(t, 0.7, 1.5);
        tools.syringe.userData.juice.scale.y = Math.max(0.05, 1 - push);
        // Thumb on the plunger: left hand pushes down from above.
        p.hL.p.copy(sci.arms.R.grip.localToWorld(V(0, 0.14 - push * 0.05, 0)));
        p.hL.q.copy(handQuat(V(0, -1, 0.4), V(0, -1, 0)));
        p.hL.curl = 0.5;
        p.look.copy(site);
        p.jaw = 0.3 * push;
        p.brow = 1 - push;
        // The body twitches as the reagent goes in.
        if (push > 0 && push < 1) patient.holder.position.y = patient.lyingTransform().pos.y + (Math.random() < 0.4 ? 0.02 : 0);
      },
    };
  };

  // ------------------------------------------------------------- finish --
  const zap = (patient, { onCharge } = {}) => {
    const root = V(1.75, 0, SPOTS.work.z);
    const target = () => patient.m.torso.getWorldPosition(V());
    let bolt = null;
    // Limbs not sewn on yet get yanked onto their sockets by the current.
    const loose = patient.detached.filter((d) => !d.attached).map((d) => ({ d, from: d.obj.position.clone(), q: d.obj.quaternion.clone(), done: false }));
    return {
      name: 'zap', duration: 3.0, blend: 0.3,
      site: () => target(),
      events: [
        { t: 0, fn: () => empty() },
        { t: 0.75, fn: () => sfx.play('lever') },
        {
          t: 0.85, fn: () => {
            bolt = lab.fx.bolt(lab.coilTop, target(), 1.3);
            sfx.play('zap');
            onCharge?.();
          },
        },
        { t: 2.1, fn: () => sfx.play('laugh') },
      ],
      pose(t, p) {
        const pull = seg(t, 0.6, 0.85);
        lab.lever.rotation.x = -0.6 + pull * 1.3;
        lab.lever.updateMatrixWorld(true);
        const lg = lab.leverGrip.getWorldPosition(V());
        sci.stand(p, root, 0, t < 0.9 ? lg : target());
        const L = (x, y, z) => sci.local(root, 0, x, y, z);
        p.spineY = 0.35 * (1 - seg(t, 1.0, 1.4));
        p.hL.p.copy(lg).add(V(0, 0.03, 0));
        p.hL.q.copy(handQuat(V(0.3, -0.3, 0.2), V(0, -1, 0)));
        p.hL.curl = 0.9;
        p.hR.p.copy(L(-0.25, 1.05, 0.25));
        const zapping = t > 0.85 && t < 2.15;
        for (const l of loose) {
          if (l.done || t < 1.0) continue;
          const u = seg(t, 1.0, 1.45);
          const sock = patient.socketWorld(l.d);
          l.d.obj.position.copy(l.from).lerp(sock.pos, u).add(V(0, Math.sin(u * Math.PI) * 0.2, 0));
          l.d.obj.quaternion.slerpQuaternions(l.q, sock.quat, u);
          if (u >= 1) { l.done = true; patient.reattach(l.d); lab.fx.sparks(sock.pos, 10, 0xbfe8ff); sfx.play('pop'); }
        }
        if (zapping) {
          const f = seg(t, 0.85, 2.1);
          setPallor(patient.m, 0.7 * (1 - f));
          patient.closeEyes(1 - seg(t, 1.6, 2.1));
          lab.zapLight.intensity = Math.random() * 18 + 6;
          const jig = (Math.random() - 0.5) * 0.03;
          patient.holder.position.y = patient.lyingTransform().pos.y + Math.abs(jig);
          if (bolt) bolt.to = target();
          if (Math.random() < 0.5) lab.fx.sparks(target(), 4, 0xbfe8ff);
          p.jaw = 0.3;
          p.blink = 0.7;
          p.brow = 1;
        } else lab.zapLight.intensity = 0;
        if (t > 1.4) {
          // Triumphant cackle, arms up.
          const up = seg(t, 1.4, 1.9);
          const sh = Math.sin(t * 16) * 0.04;
          p.hR.p.lerp(L(-0.38, 1.85 + sh, 0.12), up);
          p.hL.p.lerp(L(0.38, 1.85 - sh, 0.12), up);
          handQuat(V(-0.3, 1, 0), V(0, 0, 1), p.hR.q);
          handQuat(V(0.3, 1, 0), V(0, 0, 1), p.hL.q);
          p.hR.curl = p.hL.curl = 0.15;
          p.spineX = 0.1;
          p.look.copy(cam());
          p.jaw = 0.4 + Math.abs(Math.sin(t * 20)) * 0.5;
          p.brow = -0.5;
          p.blink = 0;
        }
      },
    };
  };

  const scrap = (patient, { onBinned } = {}) => {
    const center = () => patient.m.torso.getWorldPosition(V());
    let grabOff = null, releasePos = null, releaseQuat = null;
    const binTop = SPOTS.bin.clone().add(V(0, 0.55, 0));
    const root = V(1.2, 0, SPOTS.work.z);
    return {
      name: 'scrap', duration: 2.9, blend: 0.3, collide: false, // he hugs the body to carry it
      site: () => center(),
      events: [
        { t: 0, fn: () => empty() },
        { t: 0.65, fn: () => sfx.play('grunt') },
        { t: 1.75, fn: () => sfx.play('whoosh') },
        {
          t: 2.3, fn: () => {
            patient.holder.visible = false;
            for (const d of patient.detached) d.obj.visible = false;
            lab.binLid.rotation.x = 0;
            sfx.play('clang');
            lab.fx.smoke(binTop, 10, 0x4a5a3a);
            onBinned?.();
          },
        },
      ],
      pose(t, p) {
        const c = center();
        sci.stand(p, root, 0, c);
        p.spineX = 0.5 - seg(t, 0.6, 1.2) * 0.25;
        const grab = seg(t, 0, 0.55);
        const lift = seg(t, 0.6, 1.25);
        const swing = seg(t, 1.25, 1.75);
        const L = (x, y, z) => sci.local(root, 0, x, y, z);
        const mid0 = c.clone().add(V(0, 0.1, -0.08));
        const midLift = mid0.clone().add(V(-0.1, 0.16, 0.06));
        const midSwing = L(-0.32, 1.22, 0.5); // carried low and forward, clear of his head and chest
        let mid = mid0.clone();
        if (t >= 0.6 && t < 1.75) mid = mid0.clone().lerp(midLift, lift).lerp(midSwing, swing);
        if (t >= 0.6 && t < 1.75) {
          if (!grabOff) grabOff = patient.holder.position.clone().sub(mid0);
          patient.holder.position.copy(mid).add(grabOff);
          patient.holder.rotation.z += 0; // keep orientation
          for (const d of patient.detached) if (!d.attached) d.obj.visible = false;
        }
        if (t >= 1.75 && t < 2.3) {
          if (!releasePos) { releasePos = patient.holder.position.clone(); releaseQuat = patient.holder.quaternion.clone(); }
          const u = (t - 1.75) / 0.55;
          const pos = releasePos.clone().lerp(binTop, u).add(V(0, Math.sin(u * Math.PI) * 0.5, 0));
          patient.holder.position.copy(pos);
          patient.holder.quaternion.copy(releaseQuat).multiply(new THREE.Quaternion().setFromAxisAngle(V(0, 0, 1), u * 4));
        }
        const spread = V(0.25, 0, 0); // a wide grip keeps his arms beside, not in front of, his face
        const hm = t < 1.75 ? mid : L(-0.34, 1.12, 0.58); // release low and forward, arms clear of his head
        p.hR.p.copy(mid0).sub(spread).lerp(hm.clone().sub(spread), t > 0.6 ? 1 : 0);
        p.hL.p.copy(mid0).add(spread).lerp(hm.clone().add(spread), t > 0.6 ? 1 : 0);
        if (t < 0.6) { p.hR.p.lerp(L(-0.25, 0.9, 0.12), 1 - grab); p.hL.p.lerp(L(0.25, 0.9, 0.12), 1 - grab); }
        p.hR.q.copy(handQuat(V(0.5, -1, 0.2), V(1, 0, 0)));
        p.hL.q.copy(handQuat(V(-0.5, -1, 0.2), V(-1, 0, 0)));
        p.hR.curl = p.hL.curl = t > 0.5 && t < 1.75 ? 0.9 : 0.3;
        p.spineY = -0.4 * swing * (1 - seg(t, 2.0, 2.4));
        p.jaw = t > 0.6 && t < 1.75 ? 0.35 : 0.05;
        p.brow = t < 1.75 ? 1 : -0.2;
        if (t > 2.3) {
          // Dust off hands.
          const clap = Math.abs(Math.sin(t * 18)) * 0.06;
          p.hR.p.copy(L(-0.04 - clap, 1.05, 0.33));
          p.hL.p.copy(L(0.04 + clap, 1.05, 0.33));
          handQuat(V(0, 0.4, 1), V(1, 0, 0), p.hR.q);
          handQuat(V(0, 0.4, 1), V(-1, 0, 0), p.hL.q);
          p.look.copy(cam());
          p.spineX = 0.25;
        }
        if (t > 2.4) lab.binLid.rotation.x = -1.2 * seg(t, 2.5, 2.85);
      },
    };
  };

  // ------------------------------------------------------------ cleanup --
  const AT = V(0.25, 0, 0.15);
  const throwThing = (makeProp, { onRelease, sound = 'whoosh', dur = 2.3, release = 1.7 } = {}) => {
    const prop = makeProp();
    return {
      name: 'throw', duration: dur, blend: 0.3,
      site: () => AT.clone().add(V(0, 1.3, 0)),
      events: [
        { t: 0.35, fn: () => { equip('R', null); sci.hold('R', prop); sfx.play(makeProp === P.bomb ? 'fuse' : 'slosh'); } },
        { t: release, fn: () => { sci.held.R = null; const wp = prop.getWorldPosition(V()); lab.scene.attach(prop); prop.position.copy(wp); onRelease?.(prop); sfx.play(sound); } },
      ],
      pose(t, p) {
        sci.stand(p, AT, 0, cam());
        const L = (x, y, z) => sci.local(AT, 0, x, y, z);
        const pocket = seg(t, 0, 0.3) * (1 - seg(t, 0.4, 0.7));
        const show = seg(t, 0.45, 0.8) * (1 - seg(t, 1.1, 1.4));
        const wind = seg(t, 1.1, release - 0.1);
        const fling = seg(t, release - 0.12, release + 0.05);
        let h = L(-0.25, 0.9, 0.12);
        h.lerp(L(-0.24, 0.82, 0.1), pocket);
        h.lerp(L(-0.12, 1.45, 0.38), show);
        h.lerp(L(-0.3, 1.8, -0.3), wind);
        h.lerp(L(-0.1, 1.45, 0.62), fling);
        p.hR.p.copy(h);
        handQuat(fling > 0 ? V(0, -0.2, 1) : V(0, 1, 0.3), V(1, 0, 0.5), p.hR.q);
        p.hR.curl = t > 0.35 && t < release ? 0.8 : 0.2;
        p.hL.p.copy(L(0.3, 1.1 + wind * 0.4, 0.3 - fling * 0.3));
        p.look.copy(show > 0.5 ? h : cam());
        p.spineX = 0.22 - wind * 0.25 + fling * 0.45;
        p.spineY = wind * 0.4 - fling * 0.5;
        p.jaw = 0.2 + show * Math.abs(Math.sin(t * 16)) * 0.5;
        p.brow = show > 0.3 ? -0.4 : 0.6;
      },
    };
  };

  const cower = () => ({
    name: 'cower', loop: true, blend: 0.25,
    pose(t, p) {
      sci.stand(p, AT, 0, cam());
      const L = (x, y, z) => sci.local(AT, 0, x, y, z);
      const tr = Math.sin(t * 40) * 0.008;
      p.hipsY = 0.46 + tr;
      p.spineX = 0.85;
      p.fR.copy(L(-0.17, 0.07, 0.12));
      p.fL.copy(L(0.17, 0.07, 0.12));
      p.hR.p.copy(L(-0.17, 1.12 + tr, 0.22));
      p.hL.p.copy(L(0.17, 1.12 - tr, 0.22));
      handQuat(V(0.6, 0.3, 0.2), V(0, -1, 0), p.hR.q);
      handQuat(V(-0.6, 0.3, 0.2), V(0, -1, 0), p.hL.q);
      p.look.copy(L(0, 0, 1.5));
      p.blink = 1;
      p.jaw = 0.1;
      p.brow = 1;
    },
  });

  // The nozzle rests on the coiled hose behind him when not in use.
  const restNozzle = () => {
    const nz = tools.nozzle;
    nz.parent?.remove(nz);
    lab.scene.add(nz);
    nz.position.copy(lab.hoseRest.pos);
    nz.quaternion.copy(lab.hoseRest.quat);
  };
  restNozzle();

  // Hose down the lab floor: turn round, pick the nozzle up off the coil, turn
  // back and sweep the jet side to side across the floor in front of him.
  const PICKUP = 1.2;
  const hose = () => {
    let line = null;
    const nz = tools.nozzle;
    const hoseMat = rubber(0x2f5a2a);
    const coilYaw = Math.atan2(lab.hoseRest.pos.x - AT.x, lab.hoseRest.pos.z - AT.z);
    return {
      name: 'hose', loop: true, blend: 0.3,
      events: [
        { t: 0, fn: () => { equip('L', null); equip('R', null); } },
        { t: 0.5, fn: () => { equip('R', nz); sfx.play('grunt'); } },
        { t: PICKUP, fn: () => sfx.play('hose') },
      ],
      stop() {
        if (line) { lab.scene.remove(line); line.geometry.dispose(); line = null; }
        equip('R', null);
        restNozzle();
        sfx.stop('hose');
      },
      pose(t, p) {
        if (t < PICKUP) {
          // Turn round, stoop, grab the nozzle, straighten up and turn back.
          const turn = seg(t, 0, 0.4) * (1 - seg(t, 0.7, PICKUP));
          const stoop = seg(t, 0.2, 0.45) * (1 - seg(t, 0.6, 0.95));
          const yaw = coilYaw * turn;
          sci.stand(p, AT, yaw, lab.hoseRest.pos);
          const L = (x, y, z) => sci.local(AT, yaw, x, y, z);
          p.spineX = 0.25 + stoop * 0.75;
          p.hipsY -= stoop * 0.18;
          if (t < 0.5) p.hR.p.copy(L(-0.25, 0.9, 0.15)).lerp(lab.hoseRest.pos.clone().add(V(0, 0.08, 0)), stoop);
          else p.hR.p.copy(L(-0.1, 1.05 - stoop * 0.5, 0.4 - stoop * 0.1));
          handQuat(V(0, -1, 0.4), V(1, 0, 0), p.hR.q);
          p.hR.curl = t > 0.5 ? 0.9 : 0.3;
          p.jaw = stoop * 0.3;
          p.look.copy(t < 0.7 ? lab.hoseRest.pos : cam());
          drawLine(t >= 0.5);
          return;
        }
        // Spray: a steady side-to-side sweep.
        const s = t - PICKUP;
        const aim = V(0.55 + Math.sin(s * 1.5) * 2.3, 0, 1.0 + Math.sin(s * 0.8) * 0.25);
        sci.stand(p, AT, 0, aim);
        const L = (x, y, z) => sci.local(AT, 0, x, y, z);
        const wob = Math.sin(t * 13) * 0.025;
        // Twist toward wherever the jet is going.
        p.spineY = THREE.MathUtils.clamp(Math.atan2(aim.x - AT.x, aim.z - AT.z), -1.1, 1.1) * 0.7;
        const nzPos = L(-0.1 + wob, 1.02 + wob, 0.46).add(V(Math.sin(p.spineY) * 0.3, 0, 0));
        const dir = aim.clone().sub(nzPos).normalize();
        handQuat(dir, V(1, 0, 0), p.hR.q);
        p.hR.p.copy(wristFor(nzPos, p.hR.q, V()));
        p.hR.curl = 0.9;
        p.hL.p.copy(nzPos).addScaledVector(dir, -0.12).add(V(0.2, -0.07, 0.12));
        handQuat(dir, V(-1, 0, 0), p.hL.q);
        p.hL.curl = 0.85;
        p.spineX = 0.15 + wob;
        p.jaw = 0.35;
        p.brow = 0.6;
        p.hipsY -= 0.04;
        const tipW = sci.arms.R.grip.localToWorld(nz.userData.tip.clone());
        lab.water.setJet(tipW, aim);
        drawLine(true);

        // The hose runs from the coil, along the floor by his feet, up to his hands.
        function drawLine(held) {
          if (line) { lab.scene.remove(line); line.geometry.dispose(); line = null; }
          const exit = lab.hoseExit.clone();
          const end = held ? sci.arms.R.grip.localToWorld(V(0, 0.08, 0)) : lab.hoseRest.pos.clone();
          const feet = AT.clone().add(V(-0.2, 0.03, -0.05));
          const pts = held
            ? [exit, exit.clone().lerp(feet, 0.5).setY(0.03), feet, end.clone().add(V(-0.05, -0.45, -0.05)), end]
            : [exit, exit.clone().lerp(end, 0.5).setY(0.06), end];
          line = mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 30, 0.025, 6), hoseMat);
          lab.scene.add(line);
        }
      },
    };
  };

  return { idle, greet, celebrate, walk, snap, incise, sew, attach, organ, inject, zap, scrap, throwThing, cower, hose, equip, empty, AT };
}

function lerpAngle(a, b, t) {
  let d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * t;
}
