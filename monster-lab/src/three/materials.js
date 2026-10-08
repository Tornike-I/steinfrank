// Shared material factories. Everything is matte and lumpy, like painted
// silicone and plasticine under hot studio lights.
import * as THREE from 'three';
import { textures } from './textures.js';

export function clay(color, { rough = 0.82, bump = 2.2, emissive = 0x000000, map = null } = {}) {
  const t = textures();
  const m = new THREE.MeshStandardMaterial({
    color, roughness: rough, metalness: 0, bumpMap: t.clay, bumpScale: bump, emissive, map,
  });
  m.userData.baseColor = new THREE.Color(color);
  return m;
}

export function cloth(color, { map = null, bump = 1.5 } = {}) {
  const t = textures();
  return new THREE.MeshStandardMaterial({
    color, map, roughness: 0.95, metalness: 0, bumpMap: t.fabric, bumpScale: bump,
  });
}

export function rubber(color) {
  const t = textures();
  return new THREE.MeshStandardMaterial({ color, roughness: 0.38, metalness: 0, bumpMap: t.clay, bumpScale: 0.8 });
}

export function metal(color = 0x9a9a92, { rough = 0.42, rust = true } = {}) {
  const t = textures();
  return new THREE.MeshStandardMaterial({
    color, roughness: rough, metalness: 0.75, map: rust ? t.rust : null, bumpMap: t.clay, bumpScale: 0.6,
  });
}

export function glossy(color, { emissive = 0x000000, ei = 0 } = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.12, metalness: 0, emissive, emissiveIntensity: ei });
}

export const blood = () =>
  new THREE.MeshStandardMaterial({ color: 0x7a0508, roughness: 0.18, metalness: 0.05, emissive: 0x2a0000 });

export const thread = () => new THREE.MeshStandardMaterial({ color: 0x1b140f, roughness: 0.9 });

export function glass(color = 0x9fe36a, opacity = 0.35) {
  return new THREE.MeshStandardMaterial({
    color, roughness: 0.05, metalness: 0, transparent: true, opacity, depthWrite: false,
  });
}
