// Hand-held props. Each tool points its business end down local -Y from the
// grip, and reports `tip` (grip-local) so IK can place the tip exactly.
import * as THREE from 'three';
import { metal, rubber, glass, glossy, clay } from '../three/materials.js';
import { mesh, sausage } from '../three/geom.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

export function scalpel() {
  const g = new THREE.Group();
  const handle = mesh(new THREE.CylinderGeometry(0.011, 0.009, 0.12, 8), metal(0x8a8a82, { rough: 0.3 }));
  handle.position.y = -0.01;
  g.add(handle);
  const blade = mesh(new THREE.ConeGeometry(0.012, 0.06, 3), metal(0xe0e0e0, { rough: 0.15, rust: false }));
  blade.rotation.x = Math.PI;
  blade.scale.z = 0.25;
  blade.position.y = -0.1;
  g.add(blade);
  g.userData.tip = V(0, -0.13, 0);
  return g;
}

export function needle() {
  const g = new THREE.Group();
  const holder = mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.1, 6), metal(0x7a7a72));
  g.add(holder);
  const n = mesh(new THREE.TorusGeometry(0.025, 0.003, 4, 12, Math.PI), metal(0xdadada, { rust: false }));
  n.position.y = -0.07;
  n.rotation.z = Math.PI / 2;
  g.add(n);
  g.userData.tip = V(0, -0.095, 0);
  return g;
}

export function syringe() {
  const g = new THREE.Group();
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.16, 12), glass(0xffffff, 0.35));
  barrel.position.y = -0.04;
  g.add(barrel);
  const juice = mesh(new THREE.CylinderGeometry(0.019, 0.019, 0.15, 10), glossy(0x7aff4a, { emissive: 0x3aff1a, ei: 0.6 }), { cast: false });
  juice.position.y = -0.04;
  g.add(juice);
  const needleM = mesh(new THREE.CylinderGeometry(0.002, 0.002, 0.08, 4), metal(0xdddddd, { rust: false }));
  needleM.position.y = -0.16;
  g.add(needleM);
  const plunger = mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.01, 10), rubber(0x222222));
  plunger.position.y = 0.05;
  g.add(plunger);
  g.userData.tip = V(0, -0.2, 0);
  g.userData.juice = juice;
  return g;
}

export function bomb() {
  const g = new THREE.Group();
  const ball = mesh(new THREE.SphereGeometry(0.085, 16, 12), new THREE.MeshStandardMaterial({ color: 0x15151a, roughness: 0.35, metalness: 0.4 }));
  ball.position.y = -0.06;
  g.add(ball);
  const cap = mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.03, 8), metal(0x6a6a60));
  cap.position.y = 0.025;
  g.add(cap);
  const fuse = mesh(sausage(0.07, [0.006, 0.005]), clay(0xc8a878));
  fuse.rotation.x = Math.PI - 0.4;
  fuse.position.y = 0.04;
  g.add(fuse);
  const spark = new THREE.Mesh(new THREE.SphereGeometry(0.018, 8, 6), new THREE.MeshBasicMaterial({ color: 0xffd060 }));
  spark.position.set(0, 0.1, 0.03);
  g.add(spark);
  g.userData.spark = spark;
  return g;
}

export function flask() {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.SphereGeometry(0.07, 14, 10), glass(0xb0ff7a, 0.45));
  body.position.y = -0.07;
  g.add(body);
  const goo = mesh(new THREE.SphereGeometry(0.06, 12, 8), glossy(0x8aff3a, { emissive: 0x4aff1a, ei: 0.9 }), { cast: false });
  goo.position.y = -0.08;
  goo.scale.y = 0.8;
  g.add(goo);
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.025, 0.07, 10), glass(0xb0ff7a, 0.45));
  neck.position.y = 0.0;
  g.add(neck);
  const cork = mesh(new THREE.CylinderGeometry(0.02, 0.017, 0.03, 8), clay(0x9a7a4a));
  cork.position.y = 0.045;
  g.add(cork);
  return g;
}

export function nozzle() {
  const g = new THREE.Group();
  const grip = mesh(new THREE.CylinderGeometry(0.025, 0.03, 0.12, 10), metal(0xb8893a, { rough: 0.35 }));
  g.add(grip);
  const tip = mesh(new THREE.CylinderGeometry(0.012, 0.022, 0.1, 10), metal(0xb8893a, { rough: 0.35 }));
  tip.position.y = -0.11;
  g.add(tip);
  g.userData.tip = V(0, -0.16, 0);
  return g;
}

export function gutChunk() {
  return mesh(sausage(0.22, [0.025, 0.035, 0.03, 0.025]), clay(0xd9707a, { rough: 0.25, bump: 3 }));
}
