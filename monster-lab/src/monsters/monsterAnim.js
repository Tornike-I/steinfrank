// Procedural gait + idle animation for generated monsters, plus "verbs":
// performances blended on top while a monster works through its workflow.
//
// state: { walk, phase, excite, look, verb, verbW (0..1), talk (0..1), intensity }
//   verbs: look | read | think | compute | write | speak | cheer | sad | greet
export function animateMonster(m, t, s) {
  const walk = s.walk || 0, ph = s.phase || 0, ex = s.excite || 0;
  const verb = s.verb || null, w = verb ? (s.verbW ?? 1) : 0;
  const bob = m.gait === 'hop' ? 0 : Math.abs(Math.sin(ph)) * 0.04 * walk;
  let lean = 0, hop = 0, shiver = 0;
  if (verb === 'compute') shiver = Math.sin(t * 40) * 0.02 * w;
  if (verb === 'cheer' || verb === 'greet') hop = Math.abs(Math.sin(t * 7)) * 0.06 * w;
  if (verb === 'read' || verb === 'write') lean = 0.12 * w;
  if (verb === 'sad') lean = 0.25 * w;
  if (verb === 'look') lean = -0.08 * w;
  m.body.position.y = m.bodyLift + bob + hop;
  m.body.rotation.z = Math.sin(ph) * (m.gait === 'waddle' ? 0.12 : 0.05) * walk + Math.sin(t * 1.3 + m.personality * 6) * 0.02 + shiver;
  m.body.rotation.x = (m.gait === 'scuttle' ? 0.06 : 0.1) * walk + lean;
  const breathe = 1 + Math.sin(t * 2.2 + m.personality * 5) * 0.02;
  m.torso.scale.set(1 / Math.sqrt(breathe), breathe, 1 / Math.sqrt(breathe));

  for (const leg of m.legs) {
    leg.pivot.rotation.x = Math.sin(ph + leg.phase) * 0.6 * walk;
  }
  m.arms.forEach((a, i) => {
    const swing = -Math.sin(ph + a.phase) * 0.45 * walk;
    let x = swing + Math.sin(t * 1.4 + a.phase) * 0.06 - ex * 1.6;
    let z = a.restZ + Math.sin(t * 1.1 + a.phase) * 0.06 + ex * a.side * 0.6 * Math.abs(Math.sin(t * 10));
    if (w > 0) {
      const primary = i === 0;
      let vx = x, vz = z;
      switch (verb) {
        case 'look': vx = primary ? -1.9 + Math.sin(t * 2) * 0.1 : -0.3; vz = primary ? -a.side * 0.2 : a.restZ; break; // shade eyes
        case 'read': vx = -1.25 + Math.sin(t * 1.5 + i) * 0.05; vz = -a.side * 0.25; break;
        case 'think': vx = primary ? -2.35 : -0.5; vz = primary ? -a.side * 0.55 : a.restZ * 0.6; break; // hand to chin
        case 'compute': vx = -1.05 + Math.sin(t * 18 + i * 2) * 0.35; vz = -a.side * 0.2; break;
        case 'write': vx = primary ? -1.0 + Math.sin(t * 13) * 0.12 : -0.9; vz = primary ? -a.side * 0.3 + Math.cos(t * 13) * 0.1 : -a.side * 0.2; break;
        case 'speak': vx = -0.6 + Math.sin(t * 3.2 + i * 1.7) * 0.45; vz = a.restZ + Math.sin(t * 2.3 + i) * 0.2; break;
        case 'cheer': vx = -2.7 + Math.sin(t * 9 + i) * 0.15; vz = a.side * 0.45; break;
        case 'greet': vx = primary ? -2.6 : -0.2; vz = primary ? a.side * 0.3 + Math.sin(t * 10) * 0.35 : a.restZ; break;
        case 'sad': vx = 0.15; vz = a.restZ * 0.25; break;
        default: break;
      }
      x += (vx - x) * w;
      z += (vz - z) * w;
    }
    a.pivot.rotation.x = x;
    a.pivot.rotation.z = z;
    if (a.arm.userData.segs) {
      a.arm.userData.segs.forEach((seg, k) => {
        if (k) seg.rotation.z = Math.sin(t * (verb === 'compute' ? 8 : 2.4) + k + a.phase) * 0.45 * a.side;
      });
    }
  });
  for (const h of m.heads) {
    let yaw = (s.look ?? Math.sin(t * 0.6 + h.phase)) * 0.45;
    let pitch = Math.sin(t * 1.2 + h.phase) * 0.06 - ex * 0.25;
    let roll = Math.sin(t * 0.8 + h.phase) * 0.08;
    if (w > 0) {
      let vy = yaw, vp = pitch, vr = roll;
      switch (verb) {
        case 'look': vy = Math.sin(t * 0.9 + h.phase) * 0.8; vp = -0.4; break;
        case 'read': vy = Math.sin(t * 2.4) * 0.15; vp = 0.35; break;
        case 'think': vy = 0.2; vp = -0.3; vr = 0.25; break;
        case 'compute': vy = Math.sin(t * 7) * 0.1; vp = 0.25 + Math.sin(t * 20) * 0.03; break;
        case 'write': vp = 0.4; vy = Math.sin(t * 1.3) * 0.1; break;
        case 'speak': vy = Math.sin(t * 1.1) * 0.25; vp = -0.05 + Math.sin(t * 5) * 0.05; break;
        case 'cheer': vp = -0.35; vr = Math.sin(t * 8) * 0.15; break;
        case 'greet': vr = Math.sin(t * 5) * 0.2; vp = -0.1; break;
        case 'sad': vp = 0.55; vr = 0.15; break;
        default: break;
      }
      yaw += (vy - yaw) * w; pitch += (vp - pitch) * w; roll += (vr - roll) * w;
    }
    h.pivot.rotation.y = yaw;
    h.head.rotation.x = pitch;
    h.head.rotation.z = roll;
  }
  if (m.tail) {
    m.tail.userData.segs.forEach((seg, i) => { seg.rotation.z = Math.sin(t * (verb === 'cheer' ? 8 : 3) + i * 0.8) * 0.3 * (1 + walk); });
  }
  // Blinks (each eye slightly out of sync for extra wrongness); eyes follow the verb.
  m.eyes.forEach((e, i) => {
    const local = (t + i * 0.13 + m.personality * 3) % m.blinkRate;
    e.lid.rotation.x = local < 0.14 || (verb === 'think' && w > 0.5 && i % 2) ? Math.PI / 2 * (local < 0.14 ? 1 : 0.55) : e.open;
    let ly = (s.look ?? 0) * 0.3 + Math.sin(Math.floor(t * 0.7 + i) * 12.3) * 0.25;
    let lx = Math.sin(Math.floor(t * 0.5 + i) * 7.1) * 0.15;
    if (verb === 'read' || verb === 'write') { ly = Math.sin(t * (verb === 'read' ? 5 : 2)) * 0.45; lx = 0.35; }
    if (verb === 'think' || verb === 'look') { lx = -0.45; ly = Math.sin(t * 0.8 + i) * 0.4; }
    if (verb === 'compute') { ly = Math.sin(t * 13 + i) * 0.4; lx = Math.sin(t * 9) * 0.3; }
    e.look.rotation.y = ly;
    e.look.rotation.x = lx;
  });
  for (const mo of m.mouths) {
    let chomp = ex > 0 ? Math.abs(Math.sin(t * 12)) * ex : (Math.sin(t * 0.9 + m.personality * 9) > 0.96 ? 0.6 : 0);
    if (s.talk) chomp = Math.max(chomp, s.talk * (0.4 + Math.abs(Math.sin(t * 17)) * 0.8));
    if (verb === 'cheer') chomp = Math.max(chomp, 0.9);
    mo.cavity.scale.y = mo.baseY * (1 + chomp * 2.2);
  }
  for (const u of m.updaters) u(t, s);
}

// Height of a hop for single-legged monsters.
export function hopHeight(m, ph, walk) {
  return m.gait === 'hop' ? Math.abs(Math.sin(ph)) * 0.18 * walk : 0;
}

// Which performance each workflow step kind maps to.
export const VERB_FOR_KIND = { gather: 'look', read: 'read', think: 'think', compute: 'compute', write: 'write' };
