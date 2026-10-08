// Hose water for the cleanup: a glossy ballistic jet from the nozzle, and a
// wet film on the floor that spreads where the jet lands and keeps flowing
// toward the camera (the bottom of the screen), carrying stains with it until
// it drains away.
//
// The film is a floor-sized plane whose alpha comes from a small "wetness"
// canvas. Each frame the canvas is shifted toward +z (downhill, toward the
// viewer), slightly blurred and faded; the jet paints fresh water into it.
import * as THREE from 'three';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const SIZE = 256;
// Floor area covered by the film (metres).
const X0 = -3.2, X1 = 4.0, Z0 = -1.4, Z1 = 5.0;
const FLOW = 0.55; // m/s toward the camera
const G = 9.8;
const JET_T = 0.42; // seconds of flight from nozzle to floor

export class Water {
  constructor(lab) {
    this.lab = lab;
    this.scene = lab.scene;
    this.active = false;
    this.spraying = false;
    this.amount = 0; // how much water is on the floor (0..1, for fading)

    // Wetness map (white = wet).
    this.cv = document.createElement('canvas');
    this.cv.width = this.cv.height = SIZE;
    this.g = this.cv.getContext('2d', { willReadFrequently: true });
    this.tmp = document.createElement('canvas');
    this.tmp.width = this.tmp.height = SIZE;
    this.tg = this.tmp.getContext('2d');
    this.wet = new THREE.CanvasTexture(this.cv);

    this.ripples = rippleNormals();
    this.ripples.wrapS = this.ripples.wrapT = THREE.RepeatWrapping;
    this.ripples.repeat.set(7, 6);

    this.filmMat = new THREE.MeshStandardMaterial({
      // Wet floors mostly darken and pick up sharp glints, they don't go white.
      color: 0x1c262c, roughness: 0.03, metalness: 0.0,
      transparent: true, opacity: 0.62, alphaMap: this.wet, depthWrite: false,
      normalMap: this.ripples, normalScale: new THREE.Vector2(0.9, 0.9),
      polygonOffset: true, polygonOffsetFactor: -4,
    });
    const film = new THREE.Mesh(new THREE.PlaneGeometry(X1 - X0, Z1 - Z0), this.filmMat);
    film.rotation.x = -Math.PI / 2;
    film.position.set((X0 + X1) / 2, 0.008, (Z0 + Z1) / 2);
    film.renderOrder = 2;
    film.visible = false;
    this.film = film;
    this.scene.add(film);

    // The jet: a tube along the ballistic arc, rebuilt each frame.
    this.jetMat = new THREE.MeshStandardMaterial({
      color: 0xa8c8dc, roughness: 0.03, metalness: 0.0, transparent: true, opacity: 0.5,
      normalMap: this.ripples, normalScale: new THREE.Vector2(0.8, 0.8), depthWrite: false,
    });
    this.jet = null;
    this.from = V(); this.to = V();
    this.t = 0;
  }

  setEnv(envMap) {
    this.filmMat.envMap = envMap;
    this.filmMat.envMapIntensity = 0.22;
    this.jetMat.envMap = envMap;
    this.jetMat.envMapIntensity = 0.5;
  }

  start(creatures) {
    this.creatures = creatures;
    this.active = true;
    this.spraying = true;
    this.amount = 1;
    this.g.fillStyle = '#000';
    this.g.fillRect(0, 0, SIZE, SIZE);
    this.film.visible = true;
  }

  // Called by the hose action each step with the nozzle tip and the aim point.
  setJet(from, to) {
    this.from.copy(from);
    this.to.copy(to);
  }

  // Stop spraying; the film keeps flowing off the bottom of the screen.
  stop() {
    this.spraying = false;
    if (this.jet) { this.scene.remove(this.jet); this.jet.geometry.dispose(); this.jet = null; }
  }

  // Wetness 0..1 at a floor point.
  wetAt(x, z) {
    const [px, py] = this._px(x, z);
    if (px < 0 || py < 0 || px >= SIZE || py >= SIZE) return 0;
    return this.g.getImageData(px | 0, py | 0, 1, 1).data[0] / 255;
  }

  _px(x, z) { return [((x - X0) / (X1 - X0)) * SIZE, ((z - Z0) / (Z1 - Z0)) * SIZE]; }

  update(dt) {
    if (!this.active) return;
    this.t += dt;
    const g = this.g, tg = this.tg;

    // Flow: shift everything toward +z (down the canvas), with a soft blur and decay.
    const shift = (FLOW / (Z1 - Z0)) * SIZE * dt;
    // Wetness lives in the colour channels (black = dry): alphaMap reads green.
    tg.drawImage(this.cv, 0, 0);
    g.globalCompositeOperation = 'source-over';
    g.globalAlpha = 1;
    g.fillStyle = '#000';
    g.fillRect(0, 0, SIZE, SIZE);
    const keep = this.spraying ? Math.pow(0.6, dt) : Math.pow(0.35, dt);
    g.globalCompositeOperation = 'lighter';
    g.globalAlpha = keep * 0.5;
    g.drawImage(this.tmp, -0.6, shift);
    g.drawImage(this.tmp, 0.6, shift);
    g.globalAlpha = 1;

    if (this.spraying) {
      // Paint fresh water where the jet lands, plus a little runoff streak.
      const [px, py] = this._px(this.to.x, this.to.z);
      const r = 16 + Math.random() * 6;
      const grad = g.createRadialGradient(px, py, 0, px, py, r);
      grad.addColorStop(0, 'rgba(255,255,255,1)');
      grad.addColorStop(0.6, 'rgba(255,255,255,0.7)');
      grad.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = grad;
      g.beginPath();
      g.ellipse(px, py + r * 0.3, r, r * 1.4, 0, 0, Math.PI * 2);
      g.fill();
      g.globalCompositeOperation = 'source-over';
      this._buildJet();
      // Mist and splash at the impact.
      this.lab.fx.splash(this.to, 6);
      this.lab.fx.washSplats(this.to, 0.6);
    }
    this.wet.needsUpdate = true;

    // Ripples run downhill with the water.
    this.ripples.offset.y -= dt * FLOW * 0.9;
    this.ripples.offset.x = Math.sin(this.t * 0.7) * 0.02;

    this._carryStains(dt);

    if (!this.spraying) {
      this.amount = Math.max(0, this.amount - dt * 0.35);
      this.filmMat.opacity = 0.62 * Math.min(1, this.amount * 1.5);
      if (this.amount <= 0) {
        this.active = false;
        this.film.visible = false;
        this.filmMat.opacity = 0.62;
      }
    }
  }

  _buildJet() {
    if (this.jet) { this.scene.remove(this.jet); this.jet.geometry.dispose(); }
    const v = this.to.clone().sub(this.from).multiplyScalar(1 / JET_T).add(V(0, 0.5 * G * JET_T, 0));
    const pts = [];
    for (let i = 0; i <= 14; i++) {
      const s = (i / 14) * JET_T;
      const wob = Math.sin(this.t * 40 + i * 1.3) * 0.008 * (i / 14);
      pts.push(this.from.clone().addScaledVector(v, s).add(V(wob, -0.5 * G * s * s, wob)));
    }
    // Droplets peeling off the stream: same ballistic path, slightly scattered.
    for (let i = 0; i < 5; i++) {
      const dv = v.clone().add(V((Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.4, (Math.random() - 0.5) * 0.5));
      this.lab.fx.wet.add({ p: this.from.clone(), v: dv, life: JET_T * (0.7 + Math.random() * 0.4), max: JET_T, size: 0.004 + Math.random() * 0.006, color: 0xc8dcea, g: G, drag: 0, stretch: true });
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    const geo = new THREE.TubeGeometry(curve, 28, 0.013, 7, false);
    // Widen and break up toward the floor like a real stream.
    const pos = geo.attributes.position;
    const center = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) {
      const ring = Math.floor(i / 8) / 28;
      curve.getPointAt(Math.min(1, ring), center);
      const k = 1 + ring * 1.6 + Math.sin(this.t * 30 + ring * 20) * 0.15 * ring;
      pos.setXYZ(i, center.x + (pos.getX(i) - center.x) * k, center.y + (pos.getY(i) - center.y) * k, center.z + (pos.getZ(i) - center.z) * k);
    }
    geo.computeVertexNormals();
    this.jet = new THREE.Mesh(geo, this.jetMat);
    this.jet.renderOrder = 3;
    this.scene.add(this.jet);
  }

  // Stains under flowing water slide toward the camera, streak and dissolve.
  _carryStains(dt) {
    const decals = this.creatures?.decals || [];
    for (const d of decals) {
      const w = this.wetAt(d.pos.x, d.pos.z);
      if (w < 0.15) continue;
      d.pos.z += FLOW * dt * w * 1.1;
      d.mesh.position.z = d.pos.z;
      d.sx *= 1 - dt * 0.15 * w;
      d.streak = Math.min(2.2, (d.streak || 1) + dt * 1.2 * w);
      d.mesh.material.opacity -= dt * 0.45 * w;
      if (d.mesh.material.opacity <= 0.02) d.fade = true;
    }
  }
}

// Tileable ripple normal map from a few summed sine waves.
function rippleNormals() {
  const n = 128;
  const cv = document.createElement('canvas');
  cv.width = cv.height = n;
  const g = cv.getContext('2d');
  const img = g.createImageData(n, n);
  const h = (x, y) => {
    const a = (x / n) * Math.PI * 2, b = (y / n) * Math.PI * 2;
    return Math.sin(a * 3 + b * 2) * 0.5 + Math.sin(b * 5 - a) * 0.3 + Math.sin(a * 7 + b * 9) * 0.15;
  };
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const dx = h(x + 1, y) - h(x - 1, y), dy = h(x, y + 1) - h(x, y - 1);
      const nv = new THREE.Vector3(-dx * 2, -dy * 2, 1).normalize();
      const i = (y * n + x) * 4;
      img.data[i] = (nv.x * 0.5 + 0.5) * 255;
      img.data[i + 1] = (nv.y * 0.5 + 0.5) * 255;
      img.data[i + 2] = (nv.z * 0.5 + 0.5) * 255;
      img.data[i + 3] = 255;
    }
  }
  g.putImageData(img, 0, 0);
  return new THREE.CanvasTexture(cv);
}
