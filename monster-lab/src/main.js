// Bootstrap: one WebGL canvas behind the UI renders the active 3D scene —
// the lab (scientist, slab and the monsters roaming its floor) or a monster's
// den — scissored into the #stage rect. The DOM sits on top, untouched by any
// camera motion; creatures never block text or clicks.
import * as THREE from 'three';
import { Lab } from './lab/labScene.js';
import { LabCreatures } from './lab/labCreatures.js';
import { Overlay } from './fx/overlay.js';
import { Sfx } from './audio/sfx.js';
import { Director } from './director.js';
import { LabController } from './lab/labController.js';
import * as F from './ai/frankenstein.js';
import { MonsterController } from './chat/monsterController.js';
import { Den } from './den/den.js';
import { buildFor, getMonster } from './monsters/registry.js';
import { animateMonster } from './monsters/monsterAnim.js';
import { disposeMonster } from './monsters/monsterGen.js';
import { ChatUI } from './chat/ui.js';
import { state, onExternalChange } from './core/store.js';

const canvas = document.getElementById('gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
const MAX_PR = Math.min(window.devicePixelRatio, 1.75);
let pixelRatio = MAX_PR;
renderer.setPixelRatio(pixelRatio);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.autoClear = false;
renderer.setClearColor(0x000000, 0);

const stageEl = document.getElementById('stage');
const mainEl = document.getElementById('main');
const messagesEl = document.getElementById('messages');
const composerEl = document.getElementById('composer-wrap');

const stageRect = () => {
  const r = stageEl.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
};

// Film grain for the stage glass.
(function grain() {
  const c = document.createElement('canvas');
  c.width = c.height = 180;
  const g = c.getContext('2d');
  const img = g.createImageData(180, 180);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.random() * 255;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = Math.random() < 0.5 ? 18 : 0;
  }
  g.putImageData(img, 0, 0);
  document.documentElement.style.setProperty('--grain', `url(${c.toDataURL()})`);
})();

const lab = new Lab();
const creatures = new LabCreatures({ scene: lab.scene, fx: lab.fx, camera: lab.camera });
const overlay = new Overlay(document.getElementById('fx'));
const sfx = new Sfx();
const ui = new ChatUI();
const den = new Den();

let ctrl;
const director = new Director({
  lab, creatures, overlay, sfx,
  getStageRect: stageRect,
  getThreshold: () => state.settings.threshold,
  canCleanup: () => state.mode === 'lab',
  onMonsterBorn: () => { ctrl.persistCreatures(); ui.renderHeader(); },
  onCreaturesChanged: () => ctrl.persistCreatures(),
  onCleanupStart: () => { ui.renderHeader(); ui.renderControls(); },
  onCleanupDone: () => ctrl.onCleanupDone(),
});

const mon = new MonsterController({ den, ui });
ctrl = new LabController({ director, creatures, ui, monsters: mon });
ui.bind({
  lab: ctrl, mon,
  onSound: (on) => sfx.setEnabled(on),
  onThreshold: () => director.maybeCleanup(),
  getCount: () => creatures.count(),
  thumbnail: (def) => thumbnail(def),
  onMode: (mode) => { if (mode === 'lab') setTimeout(() => director.maybeCleanup(), 600); },
});

// --- library thumbnails: render each monster once into a small target. ----
const thumbs = new Map();
const thumbScene = new THREE.Scene();
thumbScene.add(new THREE.HemisphereLight(0xd8e0ff, 0x302010, 1.6));
const thumbKey = new THREE.DirectionalLight(0xffe0b0, 2.4);
thumbKey.position.set(-0.6, 0.8, 1);
thumbScene.add(thumbKey);
const thumbCam = new THREE.PerspectiveCamera(28, 1, 0.05, 20);
const thumbRT = new THREE.WebGLRenderTarget(128, 128, { colorSpace: THREE.SRGBColorSpace });
function thumbnail(def) {
  const key = `${def.id}:${def.seed}`;
  if (thumbs.has(key)) return thumbs.get(key);
  const m = buildFor(def);
  thumbScene.add(m.root);
  animateMonster(m, 0.5, {});
  const h = m.height;
  thumbCam.position.set(h * 0.35, h * 0.62, h * 2.6);
  thumbCam.lookAt(0, h * 0.5, 0);
  renderer.setRenderTarget(thumbRT);
  renderer.setClearColor(0x1a1c16, 1);
  renderer.setScissorTest(false);
  renderer.setViewport(0, 0, 128, 128);
  renderer.clear();
  renderer.render(thumbScene, thumbCam);
  const px = new Uint8Array(128 * 128 * 4);
  renderer.readRenderTargetPixels(thumbRT, 0, 0, 128, 128, px);
  renderer.setRenderTarget(null);
  renderer.setClearColor(0x000000, 0);
  thumbScene.remove(m.root);
  disposeMonster(m);
  const cv = document.createElement('canvas');
  cv.width = cv.height = 128;
  const g = cv.getContext('2d');
  const img = g.createImageData(128, 128);
  for (let y = 0; y < 128; y++) img.data.set(px.subarray((127 - y) * 512, (128 - y) * 512), y * 512);
  g.putImageData(img, 0, 0);
  const url = cv.toDataURL();
  thumbs.set(key, url);
  return url;
}

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false);
  overlay.resize(w, h, window.devicePixelRatio);
}
window.addEventListener('resize', resize);
resize();

if (state.mode === 'monsters') den.setMonster(mon.def);
ctrl.init();
ui.renderAll();
F.watchHealth(() => ui.renderHeader());
onExternalChange(() => ui.renderSidebar());
director.greet();

// --- clicking wandering monsters (Lab tab) --------------------------------
// The canvas sits behind the DOM, so we hit-test only where the pointer is
// over the transparent margins, never over the chat column or controls.
const threadEl = document.getElementById('thread');
const overMargin = (ev) => state.mode === 'lab' && !!ev.target.closest && !ev.target.closest('button, input, textarea, a, #composer, #sidebar, #topbar, .popover, .idea, #topic-banner');
document.addEventListener('pointermove', (ev) => {
  const hit = overMargin(ev) && creatures.pick(ev.clientX, ev.clientY, stageRect());
  mainEl.classList.toggle('over-monster', !!hit);
});
document.addEventListener('click', (ev) => {
  if (!overMargin(ev)) return;
  const c = creatures.pick(ev.clientX, ev.clientY, stageRect());
  if (c && getMonster(c.defId) && !ctrl.busy) {
    sfx.play('squeak');
    ui.openMonster(c.defId);
  }
});

// Debug handle for poking at things from the console.
window.lab = { lab, creatures, director, ctrl, mon, den, overlay, renderer, ui, debugStage: (on = true) => document.body.classList.toggle('debug-stage', on) };
if (new URLSearchParams(location.search).get('debug') === 'stage') window.lab.debugStage(true);

const timer = new THREE.Timer();
timer.connect(document);
let lastError = 0;
function frame(now) {
  // Always schedule the next frame first so one bad frame can't freeze the app.
  requestAnimationFrame(frame);
  try { step(now); } catch (err) {
    if (now - lastError > 2000) { console.error('[frame]', err); lastError = now; }
  }
}
// Adaptive resolution: integrated GPUs struggle with a full-screen, shadowed,
// bump-mapped scene, so trade pixels for frames (the stop-motion look hides it).
let perfFrames = 0, perfStart = 0;
function adaptResolution(now) {
  if (!perfStart) perfStart = now;
  perfFrames++;
  if (now - perfStart < 1000) return;
  const fps = (perfFrames * 1000) / (now - perfStart);
  perfFrames = 0; perfStart = now;
  // Unfocused/background windows get throttled rAF (~5/s); that isn't GPU load.
  if (!document.hasFocus() || fps < 10) return;
  let next = pixelRatio;
  if (fps < 40) next = Math.max(0.45, pixelRatio * (fps < 25 ? 0.7 : 0.85));
  else if (fps > 57 && pixelRatio < MAX_PR) next = Math.min(MAX_PR, pixelRatio * 1.1);
  if (Math.abs(next - pixelRatio) > 0.01) {
    pixelRatio = next;
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight, false);
  }
}

function step(now) {
  timer.update(now);
  adaptResolution(now);
  const dt = Math.min(timer.getDelta(), 1 / 8);
  const W = window.innerWidth, H = window.innerHeight;
  const r = stageRect();
  const inLab = state.mode === 'lab';
  const aspect = Math.max(0.5, r.w / Math.max(1, r.h));

  // The lab keeps running in the background so operations continue.
  lab.camera.aspect = aspect;
  // In the Lab the prompt covers the bottom of the scene, so frame everything
  // a little higher by offsetting the projection window.
  if (inLab) lab.camera.setViewOffset(r.w, r.h, 0, r.h * 0.13, r.w, r.h);
  else lab.camera.clearViewOffset();
  director.update(dt);
  lab.update(dt);
  lab.rig.update(dt);
  creatures.update(dt);
  overlay.update(dt);
  if (!inLab) {
    den.camera.aspect = aspect;
    den.update(dt);
    den.rig.update(dt);
  }

  renderer.setScissorTest(true);
  renderer.setViewport(0, 0, W, H);
  renderer.setScissor(0, 0, W, H);
  renderer.clear();
  if (r.w > 1 && r.h > 1) {
    const y = H - (r.y + r.h);
    renderer.setViewport(r.x, y, r.w, r.h);
    renderer.setScissor(r.x, y, r.w, r.h);
    if (inLab) renderer.render(lab.scene, lab.camera);
    else renderer.render(den.scene, den.camera);
  }
}
requestAnimationFrame(frame);
