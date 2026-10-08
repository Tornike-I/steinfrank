// Procedural canvas textures: thumb-printed clay, coarse fabric, grime and rust.
// Generated once at startup so nothing needs to be downloaded.
import * as THREE from 'three';
import { mulberry32 } from '../core/rng.js';

function canvas(size) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  return [c, c.getContext('2d')];
}

// Tileable value noise rendered into ImageData.
function fbm(ctx, size, rnd, { octaves = 4, base = 8, contrast = 1, offset = 128, tint = null }) {
  const grids = [];
  for (let o = 0; o < octaves; o++) {
    const n = base << o;
    const g = new Float32Array(n * n);
    for (let k = 0; k < g.length; k++) g[k] = rnd();
    grids.push({ n, g });
  }
  const img = ctx.getImageData(0, 0, size, size);
  const smooth = (t) => t * t * (3 - 2 * t);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let v = 0, amp = 0.55, tot = 0;
      for (const { n, g } of grids) {
        const fx = (x / size) * n, fy = (y / size) * n;
        const x0 = Math.floor(fx), y0 = Math.floor(fy);
        const tx = smooth(fx - x0), ty = smooth(fy - y0);
        const at = (i, j) => g[((j % n) * n) + (i % n)];
        const a = at(x0, y0), b = at(x0 + 1, y0), c = at(x0, y0 + 1), d = at(x0 + 1, y0 + 1);
        v += amp * ((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty);
        tot += amp;
        amp *= 0.5;
      }
      v = (v / tot - 0.5) * contrast * 255 + offset;
      const i = (y * size + x) * 4;
      if (tint) {
        img.data[i] = Math.max(0, Math.min(255, tint[0] + v - offset));
        img.data[i + 1] = Math.max(0, Math.min(255, tint[1] + v - offset));
        img.data[i + 2] = Math.max(0, Math.min(255, tint[2] + v - offset));
      } else {
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      }
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

function finish(c, { color = false, repeat = 1 } = {}) {
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  t.anisotropy = 4;
  if (color) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function clayBump() {
  const size = 512;
  const [c, ctx] = canvas(size);
  const rnd = mulberry32(7);
  fbm(ctx, size, rnd, { octaves: 5, base: 6, contrast: 1.3 });
  // Thumb prints: clusters of concentric arcs.
  ctx.lineWidth = 1.2;
  for (let k = 0; k < 70; k++) {
    const x = rnd() * size, y = rnd() * size, r0 = 6 + rnd() * 10, a = rnd() * Math.PI * 2;
    for (let r = r0; r < r0 + 26; r += 3) {
      ctx.strokeStyle = `rgba(${rnd() < 0.5 ? '0,0,0' : '255,255,255'},${0.08 + rnd() * 0.08})`;
      ctx.beginPath();
      ctx.ellipse(x, y, r, r * 0.75, a, 0, Math.PI * (1 + rnd() * 0.6));
      ctx.stroke();
    }
  }
  // Tool gouges and pits.
  for (let k = 0; k < 260; k++) {
    ctx.fillStyle = `rgba(0,0,0,${0.1 + rnd() * 0.25})`;
    ctx.beginPath();
    ctx.arc(rnd() * size, rnd() * size, 0.6 + rnd() * 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
  for (let k = 0; k < 40; k++) {
    ctx.strokeStyle = `rgba(0,0,0,${0.12 + rnd() * 0.15})`;
    ctx.lineWidth = 1 + rnd() * 2;
    const x = rnd() * size, y = rnd() * size;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.quadraticCurveTo(x + (rnd() - 0.5) * 60, y + (rnd() - 0.5) * 60, x + (rnd() - 0.5) * 90, y + (rnd() - 0.5) * 90);
    ctx.stroke();
  }
  return finish(c, { repeat: 2 });
}

function fabric() {
  const size = 256;
  const [c, ctx] = canvas(size);
  const rnd = mulberry32(11);
  fbm(ctx, size, rnd, { octaves: 3, base: 4, contrast: 0.6, offset: 140 });
  for (let y = 0; y < size; y += 3) {
    ctx.fillStyle = `rgba(0,0,0,${0.08 + rnd() * 0.06})`;
    ctx.fillRect(0, y, size, 1);
  }
  for (let x = 0; x < size; x += 3) {
    ctx.fillStyle = `rgba(255,255,255,${0.04 + rnd() * 0.05})`;
    ctx.fillRect(x, 0, 1, size);
  }
  return finish(c, { repeat: 3 });
}

// Colour map for stained aprons / coats: off-white with blood and grime blotches.
function stainedCloth() {
  const size = 512;
  const [c, ctx] = canvas(size);
  const rnd = mulberry32(23);
  fbm(ctx, size, rnd, { octaves: 4, base: 4, contrast: 0.35, offset: 128, tint: [222, 214, 190] });
  const blot = (col, n, rMin, rMax) => {
    for (let k = 0; k < n; k++) {
      const x = rnd() * size, y = rnd() * size, r = rMin + rnd() * (rMax - rMin);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, col.replace('A', String(0.55 + rnd() * 0.3)));
      g.addColorStop(0.7, col.replace('A', '0.25'));
      g.addColorStop(1, col.replace('A', '0'));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(x, y, r, r * (0.5 + rnd() * 0.6), rnd() * 3, 0, Math.PI * 2);
      ctx.fill();
    }
  };
  blot('rgba(110,70,40,A)', 18, 10, 60);
  blot('rgba(120,10,12,A)', 26, 4, 34);
  // Drips
  for (let k = 0; k < 22; k++) {
    const x = rnd() * size, y = rnd() * size, len = 20 + rnd() * 80;
    ctx.strokeStyle = `rgba(110,8,10,${0.5 + rnd() * 0.4})`;
    ctx.lineWidth = 2 + rnd() * 4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + (rnd() - 0.5) * 6, y + len);
    ctx.stroke();
  }
  return finish(c, { color: true });
}

function grime(tint, seed, { repeat = 1, blotches = 30 } = {}) {
  const size = 512;
  const [c, ctx] = canvas(size);
  const rnd = mulberry32(seed);
  fbm(ctx, size, rnd, { octaves: 5, base: 5, contrast: 0.55, offset: 128, tint });
  for (let k = 0; k < blotches; k++) {
    const x = rnd() * size, y = rnd() * size, r = 10 + rnd() * 70;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(20,16,8,${0.25 + rnd() * 0.3})`);
    g.addColorStop(1, 'rgba(20,16,8,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  return finish(c, { color: true, repeat });
}

function tiles(seed) {
  const size = 512;
  const [c, ctx] = canvas(size);
  const rnd = mulberry32(seed);
  const n = 8, s = size / n;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const dark = (x + y) % 2 === 0;
      const v = (dark ? 46 : 150) + (rnd() - 0.5) * 30;
      ctx.fillStyle = dark ? `rgb(${v * 0.9},${v},${v * 0.8})` : `rgb(${v},${v * 0.97},${v * 0.82})`;
      ctx.fillRect(x * s, y * s, s, s);
    }
  }
  ctx.strokeStyle = 'rgba(25,20,10,0.8)';
  ctx.lineWidth = 3;
  for (let k = 0; k <= n; k++) {
    ctx.beginPath(); ctx.moveTo(k * s, 0); ctx.lineTo(k * s, size); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, k * s); ctx.lineTo(size, k * s); ctx.stroke();
  }
  // grime + old blood
  ctx.globalCompositeOperation = 'multiply';
  const [c2, ctx2] = canvas(size);
  fbm(ctx2, size, rnd, { octaves: 4, base: 3, contrast: 0.8, offset: 170 });
  ctx.drawImage(c2, 0, 0);
  ctx.globalCompositeOperation = 'source-over';
  for (let k = 0; k < 14; k++) {
    const x = rnd() * size, y = rnd() * size, r = 8 + rnd() * 40;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, 'rgba(90,6,8,0.7)');
    g.addColorStop(1, 'rgba(90,6,8,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  return finish(c, { color: true, repeat: 3 });
}

let cache = null;
export function textures() {
  if (cache) return cache;
  cache = {
    clay: clayBump(),
    fabric: fabric(),
    cloth: stainedCloth(),
    wall: grime([96, 104, 82], 31, { repeat: 2, blotches: 50 }),
    wood: grime([92, 62, 38], 41, { repeat: 1 }),
    rust: grime([120, 108, 96], 51, { repeat: 1, blotches: 60 }),
    floor: tiles(61),
  };
  return cache;
}
