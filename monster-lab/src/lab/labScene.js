// The grubby miniature laboratory: set dressing, lights, props and the
// cinematic camera rig. Camera shots are described by a subject point, a
// viewing direction and how much of the subject must fit on screen, so they
// adapt to whatever aspect ratio the stage has.
import * as THREE from 'three';
import { clay, cloth, metal, glass, glossy, rubber, blood } from '../three/materials.js';
import { textures } from '../three/textures.js';
import { lumpy, sausage, mesh, noise3 } from '../three/geom.js';
import { Scientist } from './scientist.js';
import { LabFx } from './fx.js';
import { SPOTS } from './constants.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

export { SPOTS };

const jarColsFor = (i) => [0x9fe36a, 0xe3d26a, 0xe38a6a][i % 3];

export class Lab {
  constructor() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d0f0a);
    this.scene.fog = new THREE.Fog(0x0d0f0a, 4.5, 11);
    this.camera = new THREE.PerspectiveCamera(30, 2, 0.05, 40);
    this.camera.position.set(0, 1.5, 3);
    this.rig = new CameraRig(this.camera);
    this.t = 0;
    this._buildSet();
    this._buildLights();
    this.scientist = new Scientist();
    this.scene.add(this.scientist.root);
    this.fx = new LabFx(this.scene);
    this.tossed = [];
  }

  // Ballistic prop (thrown organs etc.) that lands on the floor and lingers.
  toss(obj, velocity, onLand) {
    this.tossed.push({ obj, v: velocity.clone(), spin: new THREE.Vector3(Math.random() * 8, Math.random() * 8, Math.random() * 8), onLand, landed: false, life: 12 });
  }

  clearTossed() {
    for (const p of this.tossed) p.obj.parent?.remove(p.obj);
    this.tossed = [];
  }

  _buildSet() {
    const t = textures();
    const s = this.scene;

    // Floor
    const floor = mesh(new THREE.PlaneGeometry(12, 8), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.75, bumpMap: t.clay, bumpScale: 1 }), { cast: false });
    floor.rotation.x = -Math.PI / 2;
    floor.position.z = 1;
    s.add(floor);

    // Back wall with lumpy plaster and tile wainscot.
    const wallMat = new THREE.MeshStandardMaterial({ map: t.wall, roughness: 0.95, bumpMap: t.clay, bumpScale: 3 });
    const wallG = new THREE.PlaneGeometry(12, 5, 60, 25);
    const wp = wallG.attributes.position;
    for (let i = 0; i < wp.count; i++) wp.setZ(i, noise3(wp.getX(i) * 1.5, wp.getY(i) * 1.5, 0, 3) * 0.04);
    wallG.computeVertexNormals();
    const wall = mesh(wallG, wallMat, { cast: false });
    wall.position.set(0, 2.5, -1.45);
    s.add(wall);
    const wains = mesh(new THREE.BoxGeometry(12, 1.0, 0.06), new THREE.MeshStandardMaterial({ map: t.floor, roughness: 0.5 }), { cast: false });
    wains.position.set(0, 0.5, -1.42);
    s.add(wains);
    const leftWall = mesh(new THREE.PlaneGeometry(8, 5), wallMat, { cast: false });
    leftWall.rotation.y = Math.PI / 2;
    leftWall.position.set(-3.2, 2.5, 1);
    s.add(leftWall);
    const rightWall = mesh(new THREE.PlaneGeometry(8, 5), wallMat, { cast: false });
    rightWall.rotation.y = -Math.PI / 2;
    rightWall.position.set(4.0, 2.5, 1);
    s.add(rightWall);
    // Heavy wooden door on the right wall, slightly ajar.
    const door = mesh(lumpy(new THREE.BoxGeometry(0.08, 2.1, 1.0, 2, 10, 6), 0.01, 3, 12), new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.9, bumpMap: t.clay, bumpScale: 2 }));
    door.position.set(3.9, 1.05, -0.4);
    door.rotation.y = 0.15;
    s.add(door);
    for (const y of [0.4, 1.6]) {
      const strap = mesh(new THREE.BoxGeometry(0.1, 0.07, 1.02), metal(0x3a3630));
      strap.position.set(3.86, y, -0.4);
      strap.rotation.y = 0.15;
      s.add(strap);
    }
    // Cabinet with a skeleton-ish clutter silhouette between door and machine.
    const cab = mesh(lumpy(new THREE.BoxGeometry(0.7, 1.8, 0.45, 4, 8, 4), 0.012, 3, 13), new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.85 }));
    cab.position.set(3.25, 0.9, -1.15);
    s.add(cab);
    for (let i = 0; i < 3; i++) {
      const b = mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.22, 10), glass(jarColsFor(i), 0.5));
      b.position.set(3.05 + i * 0.2, 1.91, -1.15);
      s.add(b);
    }

    // Stormy window with lightning flashes.
    const win = new THREE.Group();
    win.position.set(-1.55, 1.85, -1.4);
    const sky = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 1.1), new THREE.MeshBasicMaterial({ color: 0x1b2a3a }));
    win.add(sky);
    this.sky = sky;
    const frameMat = clay(0x3a2a1c, { bump: 3 });
    for (const [w, h, x, y] of [[1.05, 0.08, 0, 0.58], [1.05, 0.08, 0, -0.58], [0.08, 1.2, -0.48, 0], [0.08, 1.2, 0.48, 0], [0.05, 1.1, 0, 0], [0.9, 0.05, 0, 0.05]]) {
      const b = mesh(new THREE.BoxGeometry(w, h, 0.08), frameMat);
      b.position.set(x, y, 0.03);
      win.add(b);
    }
    s.add(win);

    // Shelves with specimen jars.
    const wood = new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.85, bumpMap: t.clay, bumpScale: 2 });
    const jarCols = [0x9fe36a, 0xe3d26a, 0x6ad8e3, 0xe38a6a, 0xb08ae3];
    this.jarLights = [];
    for (let row = 0; row < 2; row++) {
      const shelf = mesh(new THREE.BoxGeometry(2.0, 0.05, 0.32), wood);
      shelf.position.set(-0.15, 1.45 + row * 0.55, -1.27);
      shelf.rotation.z = (row ? -1 : 1) * 0.015;
      s.add(shelf);
      for (let j = 0; j < 5; j++) {
        const h = 0.18 + ((j * 7 + row * 3) % 5) * 0.04;
        const r = 0.07 + ((j + row) % 3) * 0.02;
        const x = -0.95 + j * 0.4 + row * 0.1;
        const jar = new THREE.Group();
        jar.position.set(x, 1.475 + row * 0.55, -1.25);
        const col = jarCols[(j + row * 2) % jarCols.length];
        const liquid = mesh(new THREE.CylinderGeometry(r * 0.92, r * 0.92, h * 0.8, 14), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.25, transparent: true, opacity: 0.75, roughness: 0.2 }), { cast: false });
        liquid.position.y = h * 0.4;
        jar.add(liquid);
        const shell = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 14), glass(0xffffff, 0.18));
        shell.position.y = h / 2;
        jar.add(shell);
        const lid = mesh(new THREE.CylinderGeometry(r * 1.05, r * 1.05, 0.03, 14), metal(0x6a5a40));
        lid.position.y = h + 0.015;
        jar.add(lid);
        // Floating specimen: eyeball, brain, or hand.
        const kind = (j + row) % 3;
        const spec = kind === 0
          ? mesh(new THREE.SphereGeometry(r * 0.45, 12, 8), glossy(0xf0ead8))
          : kind === 1
            ? mesh(lumpy(new THREE.SphereGeometry(r * 0.5, 12, 8), 0.01, 30, j), clay(0xe48a9a))
            : mesh(sausage(h * 0.45, [r * 0.18, r * 0.22, r * 0.3]), clay(0xd8c0a0));
        spec.position.y = h * 0.45;
        spec.userData.bob = j + row * 2;
        jar.add(spec);
        jar.userData.spec = spec;
        s.add(jar);
        this.jarLights.push(jar);
      }
    }

    // Pipes along the ceiling/wall.
    const pipeMat = metal(0x7a6a58);
    for (let i = 0; i < 3; i++) {
      const pipe = mesh(new THREE.CylinderGeometry(0.035 + i * 0.01, 0.035 + i * 0.01, 10, 10), pipeMat);
      pipe.rotation.z = Math.PI / 2;
      pipe.position.set(0, 3.0 + i * 0.14, -1.33 + i * 0.03);
      s.add(pipe);
    }

    // Operating table.
    const table = (this.table = new THREE.Group());
    table.position.copy(SPOTS.table);
    const steel = metal(0xa8a8a0, { rough: 0.38 });
    const top = mesh(lumpy(new THREE.BoxGeometry(2.0, 0.07, 0.74, 20, 1, 8), 0.006, 4, 8), steel);
    top.position.y = SPOTS.tableTop - 0.035;
    table.add(top);
    const lip = mesh(new THREE.TorusGeometry(1, 0.02, 6, 4), steel);
    lip.rotation.set(Math.PI / 2, 0, Math.PI / 4);
    lip.scale.set(1.42, 0.52, 1);
    lip.position.y = SPOTS.tableTop;
    table.add(lip);
    for (const [x, z] of [[-0.85, -0.3], [0.85, -0.3], [-0.85, 0.3], [0.85, 0.3]]) {
      const leg = mesh(new THREE.CylinderGeometry(0.03, 0.035, SPOTS.tableTop - 0.1, 8), steel);
      leg.position.set(x, (SPOTS.tableTop - 0.1) / 2 + 0.07, z);
      table.add(leg);
      const wheel = mesh(new THREE.TorusGeometry(0.045, 0.02, 6, 12), rubber(0x1a1612));
      wheel.position.set(x, 0.065, z);
      table.add(wheel);
    }
    // Old blood on the slab and dripping off the edge.
    const bm = blood();
    for (let i = 0; i < 9; i++) {
      const st = mesh(new THREE.CircleGeometry(0.04 + (i % 4) * 0.03, 10), bm, { cast: false });
      st.rotation.x = -Math.PI / 2;
      st.scale.set(1, 0.6 + (i % 3) * 0.3, 1);
      st.position.set(-0.9 + i * 0.22, SPOTS.tableTop + 0.002, ((i * 37) % 7) / 10 - 0.33);
      table.add(st);
    }
    for (let i = 0; i < 4; i++) {
      const d = mesh(sausage(0.06 + i * 0.03, [0.01, 0.012, 0.016]), bm, { cast: false });
      d.position.set(-0.6 + i * 0.45, SPOTS.tableTop - 0.03, 0.37);
      table.add(d);
    }
    s.add(table);

    // Floor stains under the table.
    for (let i = 0; i < 4; i++) {
      const st = mesh(new THREE.CircleGeometry(0.12 + i * 0.05, 14), bm, { cast: false });
      st.rotation.x = -Math.PI / 2;
      st.position.set(SPOTS.table.x - 0.6 + i * 0.4, 0.004 + i * 0.001, SPOTS.table.z + 0.1 + (i % 2) * 0.2);
      st.scale.y = 0.6;
      s.add(st);
    }

    // Hanging surgical lamp.
    const lamp = (this.lamp = new THREE.Group());
    lamp.position.set(SPOTS.lamp.x, 3.3, SPOTS.lamp.z);
    const cord = mesh(new THREE.CylinderGeometry(0.01, 0.01, 0.85, 4), rubber(0x111111));
    cord.position.y = -0.42;
    lamp.add(cord);
    const shade = mesh(new THREE.ConeGeometry(0.32, 0.3, 20, 1, true), metal(0x4a5a4a, { rough: 0.5 }));
    shade.material.side = THREE.DoubleSide;
    shade.position.y = -0.95;
    lamp.add(shade);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 8), new THREE.MeshBasicMaterial({ color: 0xfff1c0 }));
    bulb.position.y = -1.02;
    lamp.add(bulb);
    this.bulb = bulb;
    s.add(lamp);

    // Tool tray on a stand.
    const tray = new THREE.Group();
    tray.position.copy(SPOTS.tray);
    const tr = mesh(new THREE.BoxGeometry(0.5, 0.03, 0.32), steel);
    tray.add(tr);
    const stand = mesh(new THREE.CylinderGeometry(0.025, 0.025, SPOTS.tray.y, 8), steel);
    stand.position.y = -SPOTS.tray.y / 2;
    tray.add(stand);
    for (let i = 0; i < 4; i++) {
      const tool = mesh(new THREE.BoxGeometry(0.2, 0.012, 0.02), steel);
      tool.position.set(-0.1 + i * 0.05, 0.025, -0.08 + i * 0.05);
      tool.rotation.y = 0.3 * i;
      tray.add(tool);
    }
    const saw = mesh(new THREE.BoxGeometry(0.28, 0.01, 0.08), metal(0x8a7a6a));
    saw.position.set(0.05, 0.03, 0.1);
    tray.add(saw);
    s.add(tray);

    // Scrap bin with a lid.
    const bin = (this.bin = new THREE.Group());
    bin.position.copy(SPOTS.bin);
    const binMat = metal(0x6f7466, { rough: 0.55 });
    const can = mesh(new THREE.CylinderGeometry(0.27, 0.23, 0.62, 18, 3, true), binMat);
    can.material = binMat.clone(); can.material.side = THREE.DoubleSide;
    can.position.y = 0.31;
    lumpy(can.geometry, 0.01, 5, 2);
    bin.add(can);
    const bottom = mesh(new THREE.CircleGeometry(0.23, 18), binMat);
    bottom.rotation.x = -Math.PI / 2;
    bottom.position.y = 0.02;
    bin.add(bottom);
    for (const y of [0.12, 0.5]) {
      const rib = mesh(new THREE.TorusGeometry(0.26 - y * 0.05, 0.012, 6, 24), binMat);
      rib.rotation.x = Math.PI / 2;
      rib.position.y = y;
      bin.add(rib);
    }
    const lidPivot = (this.binLid = new THREE.Group());
    lidPivot.position.set(0, 0.63, -0.27);
    const lidM = mesh(new THREE.CylinderGeometry(0.29, 0.29, 0.03, 18), binMat);
    lidM.position.z = 0.27;
    const knob = mesh(new THREE.SphereGeometry(0.04, 8, 6), binMat);
    knob.position.set(0, 0.03, 0.27);
    lidPivot.add(lidM, knob);
    lidPivot.rotation.x = -1.2; // propped open
    bin.add(lidPivot);
    // A foot sticking out — previous failures.
    const foot = mesh(sausage(0.25, [0.04, 0.05, 0.06]), clay(0x8a9a7a));
    foot.position.set(0.1, 0.6, 0.05);
    foot.rotation.set(0.2, 0, -0.5);
    bin.add(foot);
    s.add(bin);

    // Tesla machine with lever.
    const mach = new THREE.Group();
    mach.position.set(2.45, 0, -0.85);
    const box = mesh(lumpy(new THREE.BoxGeometry(0.5, 1.0, 0.4, 6, 8, 6), 0.01, 3, 4), new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.8 }));
    box.position.y = 0.5;
    mach.add(box);
    const dial = mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.02, 16), glossy(0xe8e0c0));
    dial.rotation.x = Math.PI / 2;
    dial.position.set(-0.08, 0.75, 0.21);
    mach.add(dial);
    const bulbR = mesh(new THREE.SphereGeometry(0.035, 10, 8), glossy(0xff3a2a, { emissive: 0xff2a1a, ei: 0.8 }));
    bulbR.position.set(0.12, 0.85, 0.2);
    mach.add(bulbR);
    this.machineBulb = bulbR;
    const coilPost = mesh(new THREE.CylinderGeometry(0.04, 0.07, 1.0, 10), metal(0x5a4a3a));
    coilPost.position.y = 1.5;
    mach.add(coilPost);
    const copper = metal(0xc0703a, { rough: 0.3, rust: false });
    for (let i = 0; i < 9; i++) {
      const ring = mesh(new THREE.TorusGeometry(0.08 - i * 0.004, 0.015, 6, 16), copper);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 1.15 + i * 0.07;
      mach.add(ring);
    }
    const ball = mesh(new THREE.SphereGeometry(0.1, 16, 12), metal(0xcfcfc8, { rough: 0.2, rust: false }));
    ball.position.y = 2.05;
    mach.add(ball);
    // Lever pivot on the box front-left.
    const lever = (this.lever = new THREE.Group());
    lever.position.set(-0.26, 0.85, 0.1);
    const arm = mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.42, 8), metal(0x3a3a38));
    arm.position.y = 0.21;
    lever.add(arm);
    const grip = mesh(new THREE.SphereGeometry(0.045, 10, 8), rubber(0xb02a1a));
    grip.position.y = 0.44;
    lever.add(grip);
    lever.rotation.x = -0.6;
    mach.add(lever);
    this.leverGrip = grip;
    s.add(mach);
    this.coilTop = new THREE.Vector3(2.45, 2.05, -0.85);

    // Hose reel on the wall.
    const reel = new THREE.Group();
    reel.position.set(-0.85, 1.05, -1.36);
    const drum = mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.12, 18), metal(0xa83a2a, { rough: 0.5 }));
    drum.rotation.x = Math.PI / 2;
    reel.add(drum);
    const coils = mesh(new THREE.TorusGeometry(0.17, 0.035, 8, 22), rubber(0x2f5a2a));
    coils.position.z = 0.04;
    reel.add(coils);
    s.add(reel);
    this.reel = reel;

    // Bomb crate near the wall.
    const crate = mesh(lumpy(new THREE.BoxGeometry(0.45, 0.35, 0.35, 4, 4, 4), 0.012, 4, 9), new THREE.MeshStandardMaterial({ map: t.wood, roughness: 0.9 }));
    crate.position.set(-1.0, 0.175, -0.95);
    crate.rotation.y = 0.3;
    s.add(crate);
    for (let i = 0; i < 3; i++) {
      const b = mesh(new THREE.SphereGeometry(0.08, 12, 10), new THREE.MeshStandardMaterial({ color: 0x15151a, roughness: 0.4, metalness: 0.3 }));
      b.position.set(-1.12 + i * 0.12, 0.4, -0.95 + (i % 2) * 0.05);
      s.add(b);
    }
  }

  _buildLights() {
    const s = this.scene;
    s.add(new THREE.HemisphereLight(0x8090a0, 0x1a140c, 0.55));
    const key = (this.keyLight = new THREE.SpotLight(0xffe2b0, 30, 8, 0.75, 0.55, 1.6));
    key.position.set(SPOTS.lamp.x, 2.35, SPOTS.lamp.z);
    key.target.position.set(SPOTS.table.x, 0, SPOTS.table.z);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0008;
    key.shadow.normalBias = 0.02;
    s.add(key, key.target);
    // Warm practical near the idle spot.
    const fill = (this.fill = new THREE.SpotLight(0xffc890, 14, 7, 0.7, 0.6, 1.5));
    fill.position.set(0.9, 2.4, 1.6);
    fill.target.position.set(0, 1.2, 0);
    fill.castShadow = true;
    fill.shadow.mapSize.set(1024, 1024);
    fill.shadow.bias = -0.0008;
    s.add(fill, fill.target);
    const rim = new THREE.DirectionalLight(0x7aa8ff, 1.3);
    rim.position.set(-2, 3, -3);
    s.add(rim);
    const green = (this.green = new THREE.PointLight(0x8aff5a, 2.5, 3.5, 2));
    green.position.set(-0.2, 1.8, -1.0);
    s.add(green);
    this.zapLight = new THREE.PointLight(0x9ad8ff, 0, 5, 2);
    this.zapLight.position.set(2.0, 1.6, -0.3);
    s.add(this.zapLight);
  }

  update(dt) {
    this.t += dt;
    const t = this.t;
    // Lamp sway and flicker.
    this.lamp.rotation.z = Math.sin(t * 0.7) * 0.04;
    this.lamp.rotation.x = Math.sin(t * 0.53) * 0.03;
    const flick = Math.random() < 0.015 ? 0.4 : 1;
    this.keyLight.intensity = 30 * flick * (0.96 + Math.sin(t * 23) * 0.02);
    this.bulb.material.color.setScalar(flick < 1 ? 0.5 : 1).multiply(new THREE.Color(0xfff1c0));
    const lp = new THREE.Vector3(0, -1.0, 0).applyEuler(this.lamp.rotation).add(this.lamp.position);
    this.keyLight.position.copy(lp);
    // Storm outside.
    if (Math.random() < 0.004) this._storm = 1;
    this._storm = Math.max(0, (this._storm || 0) - dt * 3);
    const st = this._storm > 0 ? (Math.sin(t * 60) > 0 ? this._storm : this._storm * 0.3) : 0;
    this.sky.material.color.setRGB(0.1 + st * 0.8, 0.16 + st * 0.8, 0.23 + st * 0.8);
    this.green.intensity = 2.5 + st * 6;
    for (const jar of this.jarLights) {
      const sp = jar.userData.spec;
      sp.position.y += Math.sin(t * 1.3 + sp.userData.bob) * 0.0004;
      sp.rotation.y += dt * 0.2;
    }
    this.machineBulb.material.emissiveIntensity = 0.4 + (Math.sin(t * 5) > 0 ? 0.5 : 0);
    this.scientist.update(dt);
    this.fx.update(dt);
    this.tossed = this.tossed.filter((p) => {
      p.life -= dt;
      if (!p.landed) {
        p.v.y -= 9.8 * dt;
        p.obj.position.addScaledVector(p.v, dt);
        p.obj.rotation.x += p.spin.x * dt; p.obj.rotation.y += p.spin.y * dt;
        if (p.obj.position.y < 0.04) {
          p.obj.position.y = 0.04;
          p.obj.rotation.set(0, p.obj.rotation.y, Math.PI / 2);
          p.landed = true;
          p.onLand?.();
        }
      }
      if (p.life <= 0) { p.obj.parent?.remove(p.obj); return false; }
      return true;
    });
  }
}

// ---------------------------------------------------------------- camera ----
export class CameraRig {
  constructor(camera) {
    this.camera = camera;
    this.pos = camera.position.clone();
    this.target = new THREE.Vector3(0, 1.3, 0);
    this.shot = null;
    this.speed = 2.2;
    this.t = 0;
    this.shake = 0;
  }
  // shot: { target: Vector3 | () => Vector3, dir: Vector3, fitH, fitW, fov }
  set(shot, { speed = 2.2, cut = false } = {}) {
    this.shot = shot;
    this.speed = speed;
    if (cut) { this._desired(); this.pos.copy(this._dp); this.target.copy(this._dt); }
  }
  _desired() {
    const s = this.shot;
    const aspect = this.camera.aspect;
    const fov = THREE.MathUtils.degToRad(s.fov ?? 30);
    const tgt = typeof s.target === 'function' ? s.target() : s.target;
    const hfov = 2 * Math.atan(Math.tan(fov / 2) * aspect);
    const dH = (s.fitH ?? 1) / (2 * Math.tan(fov / 2));
    const dW = (s.fitW ?? 0) / (2 * Math.tan(hfov / 2));
    // Width matters on wide stages, but never let it push the camera miles away on narrow ones.
    const d = Math.max(dH, Math.min(dW, dH * 1.5));
    this._dt = tgt.clone();
    this._dp = tgt.clone().addScaledVector(s.dir.clone().normalize(), d);
    this._fov = s.fov ?? 30;
  }
  update(dt) {
    if (!this.shot) return;
    this.t += dt;
    this._desired();
    const k = 1 - Math.exp(-dt * this.speed);
    this.pos.lerp(this._dp, k);
    this.target.lerp(this._dt, Math.min(1, k * 1.3));
    this.camera.fov += (this._fov - this.camera.fov) * k;
    // Gentle handheld drift, plus shake on explosions.
    const drift = V(Math.sin(this.t * 0.31) * 0.012, Math.sin(this.t * 0.47) * 0.008, 0);
    this.shake = Math.max(0, this.shake - dt * 2.5);
    const sh = this.shake * 0.05;
    const jit = V((Math.random() - 0.5) * sh, (Math.random() - 0.5) * sh, 0);
    this.camera.position.copy(this.pos).add(drift).add(jit);
    this.camera.lookAt(this.target.clone().add(jit.multiplyScalar(0.5)));
    this.camera.updateProjectionMatrix();
  }
}
