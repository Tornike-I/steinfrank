// The Den: where a library monster lives and works. Workflow events drive its
// performance (verbs), a thought bubble shows the current step, and the
// camera drifts between full-body, "thinking" and "working" framings.
import * as THREE from 'three';
import { CameraRig } from '../lab/labScene.js';
import { LabFx } from '../lab/fx.js';
import { textures } from '../three/textures.js';
import { clay, metal, glass, rubber, glossy } from '../three/materials.js';
import { lumpy, mesh, sausage } from '../three/geom.js';
import { buildFor } from '../monsters/registry.js';
import { disposeMonster, faceOf } from '../monsters/monsterGen.js';
import { animateMonster, VERB_FOR_KIND } from '../monsters/monsterAnim.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const RUG_H = 0.03; // the round mat the monster stands on

const MOODS = {
  weather: { wall: 0x4a5a6a, light: 0xcfe0ff, fill: 0x6aa0ff },
  dossier: { wall: 0x6a5a3a, light: 0xffe2b0, fill: 0xffb060 },
  research: { wall: 0x4a3a5a, light: 0xffe0c0, fill: 0xb08aff },
  writer: { wall: 0x3a3a5a, light: 0xffd8a0, fill: 0x7a8aff },
  numbers: { wall: 0x3a5a3a, light: 0xe8ffd0, fill: 0x7aff7a },
  generic: { wall: 0x4a4a3a, light: 0xffe2b0, fill: 0x9aff6a },
};

export class Den {
  constructor() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d0f0a);
    this.scene.fog = new THREE.Fog(0x0d0f0a, 4, 10);
    this.camera = new THREE.PerspectiveCamera(30, 2, 0.05, 30);
    this.rig = new CameraRig(this.camera);
    this.fx = new LabFx(this.scene);
    this.t = 0;
    this._acc = 1;
    this.state = { verb: 'greet', verbW: 0, talk: 0, intensity: 0 };
    this.targetVerb = null;
    this.room = new THREE.Group();
    this.scene.add(this.room);
    this._lights();
    this._bubble();
    this.def = null;
    this.m = null;
  }

  // Lit like the laboratory: a warm hanging lamp overhead, a warm fill from
  // the front, a cool rim from behind and a green glow from the jars.
  _lights() {
    const s = this.scene;
    s.add(new THREE.HemisphereLight(0x8090a0, 0x1a140c, 0.6));
    this.key = new THREE.SpotLight(0xffe2b0, 32, 8, 0.75, 0.55, 1.6);
    this.key.position.set(0.1, 2.45, 0.35);
    this.key.target.position.set(0, 0.6, 0);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(1024, 1024);
    this.key.shadow.bias = -0.0008;
    s.add(this.key, this.key.target);
    this.rim = new THREE.DirectionalLight(0x7aa8ff, 1.4);
    this.rim.position.set(-2, 2.5, -2.5);
    s.add(this.rim);
    this.front = new THREE.SpotLight(0xffc890, 18, 9, 0.8, 0.7, 1.4);
    this.front.position.set(0.5, 3.0, 3.4);
    this.front.target.position.set(0, 0.8, 0);
    s.add(this.front, this.front.target);
    this.fill = new THREE.PointLight(0x8aff5a, 2.5, 3.5, 2);
    this.fill.position.set(-1.3, 1.8, -0.9);
    s.add(this.fill);
  }

  // ---------------------------------------------------------------- room
  _buildRoom(theme) {
    for (const c of [...this.room.children]) this.room.remove(c);
    const t = textures();
    const mood = MOODS[theme] || MOODS.generic;
    const floor = mesh(new THREE.PlaneGeometry(10, 8), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.75, bumpMap: t.clay, bumpScale: 1 }), { cast: false });
    floor.rotation.x = -Math.PI / 2;
    this.room.add(floor);
    // The same grimy plaster, tile wainscot and pipes as the laboratory.
    const wallMat = new THREE.MeshStandardMaterial({ map: t.wall, roughness: 0.95, bumpMap: t.clay, bumpScale: 3 });
    const wallG = new THREE.PlaneGeometry(10, 5, 50, 25);
    const wp = wallG.attributes.position;
    for (let i = 0; i < wp.count; i++) wp.setZ(i, Math.sin(wp.getX(i) * 2.1) * Math.cos(wp.getY(i) * 1.7) * 0.03);
    wallG.computeVertexNormals();
    const wall = mesh(wallG, wallMat, { cast: false });
    wall.position.set(0, 2.5, -1.4);
    this.room.add(wall);
    for (const sx of [-1, 1]) {
      const side = mesh(new THREE.PlaneGeometry(6, 5), wallMat, { cast: false });
      side.rotation.y = -sx * Math.PI / 2;
      side.position.set(sx * 3.2, 2.5, 1.3);
      this.room.add(side);
    }
    const wains = mesh(new THREE.BoxGeometry(10, 1.0, 0.06), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.5 }), { cast: false });
    wains.position.set(0, 0.5, -1.37);
    this.room.add(wains);
    const pipeMat = metal(0x7a6a58);
    for (let i = 0; i < 3; i++) {
      const pipe = mesh(new THREE.CylinderGeometry(0.035 + i * 0.01, 0.035 + i * 0.01, 10, 10), pipeMat);
      pipe.rotation.z = Math.PI / 2;
      pipe.position.set(0, 2.95 + i * 0.14, -1.28 + i * 0.03);
      this.room.add(pipe);
    }
    // The hanging surgical lamp, as in the lab, right above the monster.
    const lamp = new THREE.Group();
    lamp.position.set(0.1, 3.2, 0.35);
    const cord = mesh(new THREE.CylinderGeometry(0.01, 0.01, 0.6, 4), rubber(0x111111));
    cord.position.y = -0.3;
    lamp.add(cord);
    const shade = mesh(new THREE.ConeGeometry(0.3, 0.28, 20, 1, true), metal(0x4a5a4a, { rough: 0.5 }));
    shade.material.side = THREE.DoubleSide;
    shade.position.y = -0.72;
    lamp.add(shade);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 8), new THREE.MeshBasicMaterial({ color: 0xfff1c0 }));
    bulb.position.y = -0.79;
    lamp.add(bulb);
    this.room.add(lamp);
    this.lampG = lamp;
    const rug = mesh(lumpy(new THREE.CylinderGeometry(0.75, 0.78, RUG_H, 28), 0.01, 3, 2), clay(0x6a2a2a, { rough: 1, bump: 3 }), { cast: false });
    rug.position.y = RUG_H / 2;
    this.room.add(rug);
    // Each topic tints only the jar glow; the lamp stays the lab's warm light.
    this.fill.color.set(mood.fill);
    const wood = new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.85 });

    if (theme === 'weather') {
      const win = new THREE.Group();
      win.position.set(-1.3, 1.6, -1.38);
      this.sky = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 1.2), new THREE.MeshBasicMaterial({ color: 0x2a3a4a }));
      win.add(this.sky);
      for (const [w, h, x, y] of [[1.25, 0.08, 0, 0.62], [1.25, 0.08, 0, -0.62], [0.08, 1.3, -0.58, 0], [0.08, 1.3, 0.58, 0], [0.05, 1.2, 0, 0]]) {
        const b = mesh(new THREE.BoxGeometry(w, h, 0.08), clay(0x3a2a1c));
        b.position.set(x, y, 0.03);
        win.add(b);
      }
      this.room.add(win);
      const baro = new THREE.Group();
      baro.position.set(1.2, 1.7, -1.35);
      baro.add(mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.05, 24), metal(0xb8893a)));
      baro.children[0].rotation.x = Math.PI / 2;
      const face = new THREE.Mesh(new THREE.CircleGeometry(0.19, 24), new THREE.MeshStandardMaterial({ color: 0xf0e8d0 }));
      face.position.z = 0.03;
      baro.add(face);
      this.needle = mesh(new THREE.BoxGeometry(0.015, 0.17, 0.01), new THREE.MeshBasicMaterial({ color: 0x8a1010 }));
      this.needle.geometry.translate(0, 0.07, 0);
      this.needle.position.z = 0.04;
      baro.add(this.needle);
      this.room.add(baro);
    } else if (theme === 'dossier') {
      const cab = mesh(lumpy(new THREE.BoxGeometry(0.6, 1.3, 0.5, 3, 6, 3), 0.01, 3, 4), metal(0x6a7060, { rough: 0.5 }));
      cab.position.set(1.35, 0.65, -1.1);
      this.room.add(cab);
      for (let i = 0; i < 3; i++) {
        const h = mesh(new THREE.BoxGeometry(0.15, 0.03, 0.03), metal(0xcfcfc0));
        h.position.set(1.35, 0.3 + i * 0.4, -0.84);
        this.room.add(h);
      }
      const board = mesh(new THREE.BoxGeometry(1.3, 0.9, 0.04), clay(0xa87a4a, { bump: 4 }));
      board.position.set(-1.1, 1.7, -1.37);
      this.room.add(board);
      const pins = [];
      for (let i = 0; i < 6; i++) {
        const note = mesh(new THREE.BoxGeometry(0.18, 0.14, 0.005), clay([0xf0e8d0, 0xf0e070, 0xd0f0f0][i % 3], { bump: 0.4 }));
        const p = V(-1.6 + (i % 3) * 0.45 + Math.random() * 0.1, 1.5 + Math.floor(i / 3) * 0.4, -1.34);
        note.position.copy(p);
        note.rotation.z = (Math.random() - 0.5) * 0.3;
        this.room.add(note);
        pins.push(p.clone().add(V(0, 0.05, 0.01)));
      }
      const pts = [pins[0], pins[4], pins[2], pins[3], pins[5]];
      const string = mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0), 40, 0.004, 4), new THREE.MeshBasicMaterial({ color: 0xb01010 }), { cast: false });
      this.room.add(string);
    } else if (theme === 'research') {
      for (let row = 0; row < 3; row++) {
        const shelf = mesh(new THREE.BoxGeometry(1.6, 0.05, 0.3), wood);
        shelf.position.set(-1.0, 0.6 + row * 0.55, -1.25);
        this.room.add(shelf);
        let x = -1.75;
        while (x < -0.3) {
          const w = 0.05 + Math.random() * 0.06, h = 0.28 + Math.random() * 0.15;
          const b = mesh(new THREE.BoxGeometry(w, h, 0.22), clay([0x7a2a2a, 0x2a4a6a, 0x4a6a2a, 0x6a4a2a, 0x5a3a6a][Math.floor(Math.random() * 5)], { bump: 1 }));
          b.position.set(x + w / 2, 0.625 + row * 0.55 + h / 2, -1.25);
          b.rotation.z = Math.random() < 0.15 ? 0.25 : 0;
          this.room.add(b);
          x += w + 0.008;
        }
      }
      const candle = mesh(new THREE.CylinderGeometry(0.04, 0.045, 0.2, 10), clay(0xf0e8c8));
      candle.position.set(1.0, 0.1, -0.6);
      this.room.add(candle);
    } else if (theme === 'writer') {
      const desk = mesh(lumpy(new THREE.BoxGeometry(1.2, 0.06, 0.6, 6, 1, 4), 0.006, 3, 5), wood);
      desk.position.set(1.15, 0.72, -0.85);
      this.room.add(desk);
      for (const [x, z] of [[0.62, -0.6], [1.68, -0.6], [0.62, -1.1], [1.68, -1.1]]) {
        const leg = mesh(new THREE.BoxGeometry(0.05, 0.72, 0.05), wood);
        leg.position.set(x, 0.36, z);
        this.room.add(leg);
      }
      const ink = mesh(new THREE.CylinderGeometry(0.05, 0.06, 0.08, 10), glass(0x1a1a4a, 0.85));
      ink.position.set(1.4, 0.79, -0.8);
      this.room.add(ink);
      for (let i = 0; i < 7; i++) {
        const ball = mesh(lumpy(new THREE.IcosahedronGeometry(0.06, 1), 0.02, 20, i), clay(0xf2eedc, { bump: 2 }));
        ball.position.set(-0.4 + Math.random() * 2.0, 0.06, -0.3 - Math.random() * 0.8);
        this.room.add(ball);
      }
    } else if (theme === 'numbers') {
      const cv = document.createElement('canvas');
      cv.width = 512; cv.height = 300;
      const g = cv.getContext('2d');
      g.fillStyle = '#1e2a1e'; g.fillRect(0, 0, 512, 300);
      g.strokeStyle = 'rgba(240,240,220,0.85)'; g.fillStyle = 'rgba(240,240,220,0.85)';
      g.font = '34px "Comic Sans MS", cursive';
      ['2 + 2 = 5?', '∑ guts = 7', 'π ≈ 3.14159…', '17% × ☠', 'x² − 1 = (x−1)(x+1)'].forEach((l, i) => g.fillText(l, 24 + (i % 2) * 30, 50 + i * 52));
      const tex = new THREE.CanvasTexture(cv);
      tex.colorSpace = THREE.SRGBColorSpace;
      const board = mesh(new THREE.BoxGeometry(1.7, 1.0, 0.04), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.9 }));
      board.position.set(-0.6, 1.75, -1.37);
      this.room.add(board);
      const frame = mesh(new THREE.BoxGeometry(1.8, 1.1, 0.03), wood);
      frame.position.set(-0.6, 1.75, -1.39);
      this.room.add(frame);
    } else {
      for (let i = 0; i < 4; i++) {
        const jar = mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.28, 12), glass([0x9fe36a, 0xe3d26a, 0x6ad8e3, 0xe38a6a][i], 0.5));
        jar.position.set(-1.5 + i * 0.35, 1.6, -1.25);
        this.room.add(jar);
      }
      const shelf = mesh(new THREE.BoxGeometry(1.6, 0.05, 0.3), wood);
      shelf.position.set(-0.95, 1.44, -1.25);
      this.room.add(shelf);
    }
    this.room.traverse((o) => { if (o.isMesh) o.receiveShadow = true; });
  }

  // -------------------------------------------------------------- bubble
  _bubble() {
    const cv = document.createElement('canvas');
    cv.width = 640; cv.height = 300;
    this._bubbleCanvas = cv;
    this._bubbleTex = new THREE.CanvasTexture(cv);
    this._bubbleTex.colorSpace = THREE.SRGBColorSpace;
    this.bubble = new THREE.Sprite(new THREE.SpriteMaterial({ map: this._bubbleTex, transparent: true, depthTest: false }));
    this.bubble.renderOrder = 10;
    this.bubble.visible = false;
    this.scene.add(this.bubble);
    this.puffs = [0.06, 0.09].map((r) => {
      const p = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8), new THREE.MeshBasicMaterial({ color: 0xf4eedc, depthTest: false }));
      p.renderOrder = 10;
      p.visible = false;
      this.scene.add(p);
      return p;
    });
    this.bubbleText = '';
    this.bubbleA = 0;
  }

  _drawBubble(text, sub) {
    const g = this._bubbleCanvas.getContext('2d');
    g.clearRect(0, 0, 640, 300);
    g.fillStyle = '#f4eedc';
    g.strokeStyle = '#2a1a10';
    g.lineWidth = 8;
    g.beginPath();
    for (let i = 0; i <= 20; i++) {
      const a = (i / 20) * Math.PI * 2;
      const r = 1 + Math.sin(a * 9) * 0.05;
      g.lineTo(320 + Math.cos(a) * 290 * r, 150 + Math.sin(a) * 120 * r);
    }
    g.closePath();
    g.fill();
    g.stroke();
    g.fillStyle = '#2a1a10';
    g.textAlign = 'center';
    g.font = '700 44px Inter, system-ui, sans-serif';
    const words = text.split(' ');
    const lines = [];
    let line = '';
    for (const w of words) {
      if ((line + ' ' + w).trim().length > 20) { lines.push(line.trim()); line = w; } else line += ' ' + w;
    }
    lines.push(line.trim());
    lines.slice(0, 3).forEach((l, i, arr) => g.fillText(l, 320, 140 - (arr.length - 1) * 26 + i * 52 + 8));
    if (sub) {
      g.font = '600 26px Inter, system-ui, sans-serif';
      g.fillStyle = '#7a5a3a';
      g.fillText(sub, 320, 250);
    }
    this._bubbleTex.needsUpdate = true;
  }

  // ------------------------------------------------------------- monster
  setMonster(def) {
    if (this.def?.id === def?.id && this.def?.seed === def?.seed) return;
    if (this.m) { this.scene.remove(this.m.root); disposeMonster(this.m); this.m = null; }
    this.def = def;
    if (!def) return;
    this._buildRoom(def.theme);
    this.m = buildFor(def);
    this.m.root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    this.scene.add(this.m.root);
    this.h = this.m.height;
    this.ground = this._standHeight();
    this.hideBubble();
    this.perform('greet', 2.2);
    this._shot('idle', true);
  }

  // How high the monster must stand so its lowest point rests on top of the
  // mat (the rug is bumpy, so a hair above its top). Fliers hover anyway.
  _standHeight() {
    animateMonster(this.m, 0, { walk: 0, excite: 0 });
    this.m.root.position.y = 0;
    this.m.root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.m.root, true);
    const top = RUG_H + 0.016; // lumpy() bumps the mat up to ~1.5 cm
    return box.min.y < top + 0.05 ? top - box.min.y : 0;
  }

  // Arriving from a summon: start right in the monster's face (where the lab
  // left off) and pull back to the usual waist-up framing.
  enter() {
    if (!this.m) return;
    this.m.root.position.y = this.ground;
    this.m.root.updateMatrixWorld(true);
    const f = faceOf(this.m);
    this.rig.set({ target: f.pos.clone(), dir: V(0.03, 0.02, 1), fitH: Math.max(0.3, f.r * 2.6), fov: 30 }, { cut: true });
    this.perform('greet', 2.2);
    clearTimeout(this._enterT);
    this._enterT = setTimeout(() => this.rig.set(this._shots().idle, { speed: 1.5 }), 120);
  }

  _shot(kind, cut = false) {
    this.rig.set(this._shots()[kind], { speed: 1.8, cut });
  }

  _shots() {
    const h = this.h || 1;
    return {
      // Waist-up, facing the viewer. The projection is offset (see main.js) so
      // the monster sits in the upper part of the screen above the chat panel.
      idle: { target: V(0.05 * h, h * 0.76, 0), dir: V(0.1, 0.07, 1), fitH: h * 1.8, fov: 30 },
      think: { target: V(0.18 * h, h * 0.8, 0), dir: V(0.2, 0.06, 1), fitH: h * 1.75, fov: 30 },
      work: { target: V(0.05 * h, h * 0.74, 0.05), dir: V(-0.15, 0.12, 1), fitH: h * 1.65, fov: 30 },
      speak: { target: V(0, h * 0.77, 0), dir: V(0.05, 0.06, 1), fitH: h * 1.7, fov: 30 },
    };
  }

  // Play a verb; `dur` makes it temporary, falling back to idle.
  perform(verb, dur = 0) {
    this.state.verb = verb;
    this.state.verbW = 0;
    this._verbUntil = dur ? this.t + dur : 0;
  }

  showBubble(text, sub) {
    this.bubbleText = text;
    this._drawBubble(text, sub);
    this.bubble.visible = true;
    this.puffs.forEach((p) => { p.visible = true; });
  }
  hideBubble() {
    this.bubble.visible = false;
    this.puffs.forEach((p) => { p.visible = false; });
  }

  // --------------------------------------------------- workflow lifecycle
  onStep(step) {
    if (step.limb && this.m) this.m.activeLimbs.add(step.limb);
    const verb = VERB_FOR_KIND[step.kind] || 'think';
    this.perform(verb);
    this.state.intensity = 1;
    this.showBubble(step.label + '…', `step ${step.index + 1} of ${step.total}`);
    this._shot(verb === 'think' || verb === 'look' ? 'think' : 'work');
    if (verb === 'compute' && this.m) this.fx.sparks(this._headTop(), 10, 0xaaff7a);
  }
  onStepDone(step) {
    if (step.limb && this.m) this.m.activeLimbs.delete(step.limb);
  }
  onQuestion(text) {
    this.perform('think');
    this.showBubble(text.length > 70 ? text.slice(0, 67) + '…' : text, 'waiting for your answer');
    this._shot('think');
  }
  onToken() {
    if (this.state.verb !== 'speak') { this.perform('speak'); this.hideBubble(); this._shot('speak'); }
    this.state.talk = 1;
  }
  onDone() {
    this.m?.activeLimbs.clear();
    this.state.intensity = 0;
    this.hideBubble();
    this.perform('cheer', 1.8);
    this._shot('idle');
  }
  onFail(stopped) {
    this.m?.activeLimbs.clear();
    this.state.intensity = 0;
    this.hideBubble();
    this.perform('sad', stopped ? 1.4 : 2.6);
    this._shot('idle');
  }

  _headTop() {
    const p = V();
    this.m?.anchors.top.getWorldPosition(p);
    return p;
  }

  // ---------------------------------------------------------------- frame
  update(dt) {
    this.t += dt;
    this.fx.update(dt);
    if (this._verbUntil && this.t > this._verbUntil) { this.state.verb = null; this._verbUntil = 0; }
    if (this.sky) {
      const flash = this.state.intensity && this.def?.theme === 'weather' && Math.sin(this.t * 17) > 0.97;
      this.sky.material.color.setRGB(flash ? 0.8 : 0.16, flash ? 0.85 : 0.22, flash ? 0.9 : 0.3);
    }
    if (this.needle) this.needle.rotation.z = Math.sin(this.t * (this.state.intensity ? 6 : 0.5)) * (this.state.intensity ? 1.2 : 0.3);
    if (!this.m) return;

    // Bubble floats above the head, slightly to the side.
    if (this.bubble.visible) {
      const top = this._headTop();
      const h = this.h;
      const bob = Math.sin(this.t * 2) * 0.02 * h;
      // Beside the head, sized for the waist-up framing, and kept on screen.
      this.bubble.position.set(top.x + h * 0.62, top.y + h * 0.12 + bob, top.z + 0.1);
      this.bubble.scale.set(h * 0.82, h * 0.385, 1);
      this.camera.updateMatrixWorld();
      for (let i = 0; i < 6; i++) {
        const right = this.bubble.position.clone().add(V(h * 0.41, 0, 0)).project(this.camera);
        const upper = this.bubble.position.clone().add(V(0, h * 0.19, 0)).project(this.camera);
        const limit = this.rightLimit ?? 0.96;
        if (right.x > limit) this.bubble.position.x -= h * 0.12;
        if (upper.y > 0.92) this.bubble.position.y -= h * 0.06;
        if (right.x <= limit && upper.y <= 0.92) break;
      }
      this.puffs[0].position.set(top.x + h * 0.16, top.y + h * 0.02, top.z + 0.1);
      this.puffs[1].position.set(top.x + h * 0.26, top.y + h * 0.07 + bob * 0.5, top.z + 0.1);
      this.puffs[0].scale.setScalar(h);
      this.puffs[1].scale.setScalar(h);
    }

    // Stop-motion stepping for the puppet.
    // Animate every frame for smooth motion.
    const step = Math.min(dt, 0.1);
    const s = this.state;
    s.verbW = Math.min(1, s.verbW + step * 3.5);
    s.talk = Math.max(0, s.talk - step * 4);
    if (this.voiceLevel) s.talk = Math.max(s.talk, this.voiceLevel());
    animateMonster(this.m, this.t, { ...s, walk: 0, excite: 0 });
    const g = this.ground || 0;
    if (s.verb === 'greet' || s.verb === 'cheer') this.m.root.position.y = g + Math.abs(Math.sin(this.t * 7)) * 0.05 * this.h * s.verbW;
    else this.m.root.position.y = g;
    if (s.verb === 'think' && Math.random() < 0.15) this.fx.smoke(this._headTop().add(V(0.1, 0.05, 0)), 1, 0xe8e0d0);
  }
}
