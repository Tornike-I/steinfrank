// Fixed positions on the lab set (world units ≈ metres).
import * as THREE from 'three';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

export const SPOTS = {
  idle: V(0, 0, 0),
  work: V(1.45, 0, -0.6),
  table: V(1.5, 0, 0.1),
  tableTop: 0.92,
  bin: V(0.15, 0, -1.0),
  tray: V(0.5, 0.98, -0.42),
  lamp: V(1.5, 2.45, 0.1),
};

export const SPOTS_TABLE = { x: SPOTS.table.x, z: SPOTS.table.z, top: SPOTS.tableTop };
