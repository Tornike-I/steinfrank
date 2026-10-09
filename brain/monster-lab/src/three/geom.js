// Geometry helpers for hand-sculpted shapes: lumpy displacement, lathe
// "sausages" for limbs and bodies, and instanced stitch threads.
import * as THREE from 'three';
import { thread } from './materials.js';

// --- noise -----------------------------------------------------------------
function hash(x, y, z, s) {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647 + s * 1442695041) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
export function noise3(x, y, z, s = 0) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const xf = x - xi, yf = y - yi, zf = z - zi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf);
  const l = (a, b, t) => a + (b - a) * t;
  const c = (dx, dy, dz) => hash(xi + dx, yi + dy, zi + dz, s);
  return l(
    l(l(c(0, 0, 0), c(1, 0, 0), u), l(c(0, 1, 0), c(1, 1, 0), u), v),
    l(l(c(0, 0, 1), c(1, 0, 1), u), l(c(0, 1, 1), c(1, 1, 1), u), v),
    w,
  ) * 2 - 1;
}

// Push vertices along their normals by noise so primitives look hand-pressed.
export function lumpy(geo, amp = 0.02, freq = 6, seed = 1) {
  geo.computeVertexNormals();
  const p = geo.attributes.position, n = geo.attributes.normal;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const d = amp * (noise3(x * freq, y * freq, z * freq, seed) + 0.5 * noise3(x * freq * 2.3, y * freq * 2.3, z * freq * 2.3, seed + 9));
    p.setXYZ(i, x + n.getX(i) * d, y + n.getY(i) * d, z + n.getZ(i) * d);
  }
  geo.computeVertexNormals();
  return geo;
}

// Lathe along -Y from 0 to -len, radius profile sampled from `radii` (smooth).
// Ends are rounded so limbs look like stuffed sausages.
export function sausage(len, radii, { segs = 14, rings = 18, cap = true } = {}) {
  const pts = [];
  const sample = (t) => {
    const f = t * (radii.length - 1);
    const i = Math.min(Math.floor(f), radii.length - 2);
    const k = f - i, s = k * k * (3 - 2 * k);
    return radii[i] * (1 - s) + radii[i + 1] * s;
  };
  const r0 = radii[0], r1 = radii[radii.length - 1];
  pts.push(new THREE.Vector2(0.0001, cap ? r0 * 0.9 : 0));
  for (let i = 0; i <= rings; i++) {
    const t = i / rings;
    let r = sample(t);
    const y = -t * len;
    if (cap) {
      const c0 = Math.min(0.45, (r0 * 0.9) / len), c1 = Math.min(0.45, (r1 * 0.9) / len);
      if (t < c0) r *= Math.sqrt(Math.max(0, 1 - ((c0 - t) / c0) ** 2)) * 0.85 + 0.15;
      if (t > 1 - c1) r *= Math.sqrt(Math.max(0, 1 - ((t - (1 - c1)) / c1) ** 2)) * 0.85 + 0.15;
    }
    pts.push(new THREE.Vector2(Math.max(0.0001, r), y));
  }
  pts.push(new THREE.Vector2(0.0001, -len - (cap ? r1 * 0.9 : 0)));
  // Lathe expects increasing y; reverse.
  pts.reverse();
  return new THREE.LatheGeometry(pts, segs);
}

export function mesh(geo, mat, { cast = true, receive = true } = {}) {
  const m = new THREE.Mesh(geo, mat);
  m.castShadow = cast;
  m.receiveShadow = receive;
  return m;
}

// --- stitches ---------------------------------------------------------------
const _m = new THREE.Matrix4();
const _x = new THREE.Vector3(), _y = new THREE.Vector3(), _z = new THREE.Vector3();
const _q = new THREE.Quaternion(), _s = new THREE.Vector3();

// samples: [{ p, t, n }] position, tangent along seam, surface normal.
export function stitches(samples, { len = 0.05, thick = 0.007, cross = true, mat = null, maxCount } = {}) {
  const per = cross ? 2 : 1;
  const count = maxCount ?? samples.length * per;
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const im = new THREE.InstancedMesh(geo, mat || thread(), count);
  im.castShadow = false;
  im.receiveShadow = false;
  let k = 0;
  for (const s of samples) {
    for (let c = 0; c < per; c++) {
      _y.copy(s.n).normalize();
      _x.crossVectors(s.t, _y).normalize();
      if (cross) _x.applyAxisAngle(_y, c === 0 ? 0.6 : -0.6);
      _z.crossVectors(_x, _y).normalize();
      _m.makeBasis(_x, _y, _z);
      _q.setFromRotationMatrix(_m);
      _s.set(len, thick, thick * 1.2);
      _m.compose(s.p, _q, _s);
      im.setMatrixAt(k++, _m);
    }
  }
  im.count = Math.min(k, count);
  im.instanceMatrix.needsUpdate = true;
  return im;
}

// Ring of stitches around an axis — used where limbs are sewn onto bodies.
export function ringSamples(center, axis, radius, n = 10) {
  const a = axis.clone().normalize();
  const u = new THREE.Vector3(1, 0, 0);
  if (Math.abs(a.dot(u)) > 0.9) u.set(0, 0, 1);
  const v = new THREE.Vector3().crossVectors(a, u).normalize();
  u.crossVectors(v, a).normalize();
  const out = [];
  for (let i = 0; i < n; i++) {
    const ang = (i / n) * Math.PI * 2;
    const nrm = u.clone().multiplyScalar(Math.cos(ang)).addScaledVector(v, Math.sin(ang));
    out.push({
      p: center.clone().addScaledVector(nrm, radius),
      n: nrm,
      t: new THREE.Vector3().crossVectors(a, nrm),
    });
  }
  return out;
}

// Samples along a curve on an ellipsoid surface (center c, radii r).
export function ellipsoidLine(c, r, from, to, n = 10, lift = 1.02) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1);
    const d = from.clone().lerp(to, t).normalize();
    const p = new THREE.Vector3(d.x * r.x, d.y * r.y, d.z * r.z).multiplyScalar(lift).add(c);
    const nrm = new THREE.Vector3(d.x / r.x, d.y / r.y, d.z / r.z).normalize();
    out.push({ p, n: nrm, t: null });
  }
  for (let i = 0; i < n; i++) {
    const a = out[Math.max(0, i - 1)].p, b = out[Math.min(n - 1, i + 1)].p;
    out[i].t = b.clone().sub(a).normalize();
  }
  return out;
}
