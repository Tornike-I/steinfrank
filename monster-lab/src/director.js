// The Director turns response lifecycle events into choreography.
//
//   begin()    – generation started: walk to the slab, start cutting
//   complete() – generation finished: finish the current action, zap, reveal
//   abort()    – stopped / failed: finish quickly, scrap the body into the bin
//
// The operation loop keeps choosing surgical actions for as long as the
// response is streaming, so animation length follows generation time rather
// than a fixed cinematic. All sequences run on one serial chain; reset()
// cancels everything (used when switching conversations).
import * as THREE from 'three';
import { Patient } from './lab/patient.js';
import { makeActions } from './lab/actions.js';
import { SPOTS } from './lab/constants.js';
import { animateMonster } from './monsters/monsterAnim.js';
import { randomSeed } from './core/rng.js';
import * as P from './lab/props.js';
import { themeOf } from './monsters/themes.js';

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const CANCEL = Symbol('cancel');

const SHOTS = {
  idle: { target: V(0, 1.42, 0), dir: V(0.2, 0.05, 1), fitH: 1.35, fitW: 1.9, fov: 30 },
  wide: { target: V(1.0, 1.15, 0.0), dir: V(0.12, 0.22, 1), fitH: 1.9, fitW: 3.4, fov: 32 },
  zap: { target: V(1.85, 1.4, -0.3), dir: V(-0.25, 0.15, 1), fitH: 1.6, fitW: 2.6, fov: 30 },
  reveal: { target: V(1.5, 1.4, 0.1), dir: V(0, 0.08, 1), fitH: 1.55, fitW: 2.0, fov: 30 },
  cleanup: { target: V(0.3, 1.15, 0.1), dir: V(0.05, 0.12, 1), fitH: 1.9, fitW: 3.2, fov: 32 },
};

export class Director {
  constructor({ lab, creatures, overlay, sfx, getStageRect, getThreshold, canCleanup, onMonsterBorn, onCreaturesChanged, onCleanupStart, onCleanupDone }) {
    Object.assign(this, { lab, creatures, overlay, sfx, getStageRect, getThreshold, canCleanup, onMonsterBorn, onCreaturesChanged, onCleanupStart, onCleanupDone });
    this.sci = lab.scientist;
    this.A = makeActions(lab, sfx);
    this.epoch = 0;
    this.chain = Promise.resolve();
    this.op = null;
    this.cleaning = false;
    this.tweens = [];
    this.reveal = null;
    this.hurry = false;
    this.where = 'idle';
    lab.rig.set(SHOTS.idle, { cut: true });
    this.sci.play(this.A.idle());
    this.sci.update(0.2);
  }

  // ------------------------------------------------------------ plumbing
  _run(task) {
    const epoch = this.epoch;
    this.chain = this.chain.then(() => (epoch === this.epoch ? task(epoch) : null)).catch((e) => {
      if (e !== CANCEL) console.error(e);
    });
    return this.chain;
  }
  async _w(promise, epoch) {
    const r = await promise;
    if (epoch !== this.epoch) throw CANCEL;
    return r;
  }
  _play(action, epoch) { return this._w(this.sci.play(action), epoch); }
  _sleep(s, epoch) { return this._w(this._tween(s, () => {}), epoch); }
  _tween(dur, fn) {
    return new Promise((resolve) => this.tweens.push({ t: 0, dur, fn, resolve }));
  }
  _shot(name, opts) { this.lab.rig.set(typeof name === 'string' ? SHOTS[name] : name, opts); }
  _setHurry(on) {
    this.hurry = on;
    this.sci.timeScale = on ? 2.4 : 1;
  }

  get busy() { return !!this.op || this.cleaning; }

  // A wave on page load. Not on the chain, so the first question isn't delayed.
  greet() {
    setTimeout(() => {
      if (this.op || this._pending || this.cleaning) return;
      this.sci.play(this.A.greet()).then((r) => {
        if (r === 'done' && !this.op && !this._pending && !this.cleaning) this.sci.play(this.A.idle());
      });
    }, 600);
  }

  // --------------------------------------------------------- lifecycle API
  // def: the monster being built ({ id, seed, theme, name, ... }).
  begin({ messageId, def }) {
    if (this.op || this.reveal) this._setHurry(true);
    def ||= { seed: randomSeed(), theme: 'generic' };
    const op = { messageId, def, seed: def.seed, done: false, aborted: false, patient: null };
    this._pending = op;
    this._run((e) => this._operate(op, e));
    return op;
  }

  // Build a topic assistant's monster as a short, fixed cinematic (it no
  // longer tracks answer generation). Resolves once the monster has landed in
  // the lab; rejects if cancelled (the body is scrapped into the bin).
  create(def) {
    return new Promise((resolve, reject) => {
      const op = this.begin({ messageId: `create-${def.id}`, def });
      op.done = true;
      op.minActions = 2;
      op.monsterId = `w-${def.id}-${Date.now().toString(36)}`;
      op.onBorn = resolve;
      op.onAbort = () => reject(new Error('cancelled'));
      this._createOp = op;
    });
  }

  abortCreate() {
    const op = this._createOp;
    if (op && !op.born) this.abort({ messageId: op.messageId });
  }

  // An existing assistant answers a lab question: its wandering counterpart
  // (or a temporary stand-in if it was destroyed) turns, charges and screams.
  async summon(def) {
    const sci = this.sci;
    if (this.where === 'idle' && !this.op) {
      sci.play(this.A.celebrate(2.6, SPOTS.idle));
    }
    await this.creatures.summon(
      { seed: def.seed, theme: def.theme, defId: def.id, name: themeLabel(def) },
      {
        onScream: () => {
          this.sfx.play('scream');
          this.lab.rig.shake = 0.6;
        },
      },
    );
    if (this.where === 'idle' && !this.op) sci.play(this.A.idle());
  }

  complete({ messageId, monsterId }) {
    const op = this._find(messageId);
    if (!op) return;
    op.done = true;
    op.monsterId = monsterId;
    return op.seed;
  }

  abort({ messageId }) {
    const op = this._find(messageId);
    if (!op) return;
    op.aborted = true;
    if (op === this.op) this._setHurry(true);
  }

  _find(id) {
    if (this.op?.messageId === id) return this.op;
    if (this._pending?.messageId === id) return this._pending;
    return null;
  }

  // Cancel everything and snap back to idle (conversation switch).
  reset() {
    this.epoch++;
    for (const tw of this.tweens) tw.resolve();
    this.tweens = [];
    this.chain = Promise.resolve();
    if (this.op?.patient) this.op.patient.dispose();
    for (const o of [this.op, this._pending, this._createOp]) if (o && !o.born) o.onAbort?.();
    if (this.reveal?.patient) this.reveal.patient.dispose();
    this.op = null; this._pending = null; this.reveal = null;
    this.A.empty();
    this._hoseAction?.stop?.();
    this.sfx.stop('hose');
    for (const o of this._thrown || []) o.parent?.remove(o);
    this._thrown = [];
    this.lab.clearTossed();
    this.lab.fx.clearSplats();
    this.lab.zapLight.intensity = 0;
    this.lab.lever.rotation.x = -0.6;
    this.lab.binLid.rotation.x = -1.2;
    const wasCleaning = this.cleaning;
    this.cleaning = false;
    this._setHurry(false);
    this.where = 'idle';
    this.sci.play(this.A.idle());
    this.sci._prev = this.sci.pose; // no blend from wherever he was
    this._shot('idle', { cut: true });
    if (wasCleaning) this.onCleanupDone?.({ cancelled: true });
  }

  // ------------------------------------------------------------- surgery
  async _operate(op, e) {
    this._pending = null;
    if (op.aborted) { this._setHurry(false); op.onAbort?.(); return; } // stopped before we even started
    this.op = op;
    this._setHurry(false);
    const A = this.A;
    op.patient = new Patient(op.def, this.lab.scene);
    this._dropIn(op.patient);

    // 1–2. Start working; pull back to reveal the slab.
    this._shot('wide', { speed: 2.4 });
    if (this.where === 'idle') {
      await this._play(A.snap(), e);
      await this._play(A.walk(SPOTS.idle, SPOTS.work, 0), e);
    } else {
      await this._play(A.walk(this.sci.pose.rootPos.clone(), SPOTS.work, 0), e);
    }
    this.where = 'work';

    // 3–4. Operate for as long as the response streams.
    let last = null;
    let first = true;
    let count = 0;
    for (;;) {
      if (op.aborted) break;
      if (op.done && !first && count >= (op.minActions || 1)) break;
      const kind = first ? 'incise' : op.minActions ? (op.patient.nextDetached() ? 'attach' : 'sew') : this._nextAction(op.patient, last);
      count++;
      const action = A[kind](op.patient);
      this._frameAction(kind, action);
      await this._play(action, e);
      last = kind;
      first = false;
    }

    if (op.aborted) { await this._scrap(op, e); op.onAbort?.(); }
    else await this._finish(op, e);
    this.op = null;
    this._setHurry(false);
    this._afterOp(e);
  }

  // The body falls onto the slab from the rafters (it has to come from somewhere).
  _dropIn(patient) {
    const items = [patient.holder, ...patient.detached.map((d) => d.obj)].map((o) => ({ o, y: o.position.y }));
    const H = 2.6;
    for (const it of items) it.o.position.y += H;
    let landed = false;
    this._tween(0.55, (u) => {
      const fall = u < 0.75 ? (u / 0.75) ** 2 : 1;
      const bounce = u >= 0.75 ? Math.sin(((u - 0.75) / 0.25) * Math.PI) * 0.06 : 0;
      for (const it of items) it.o.position.y = it.y + H * (1 - fall) + bounce;
      if (u >= 0.75 && !landed) {
        landed = true;
        this.sfx.play('splat');
        this.lab.fx.blood(patient.holder.position.clone(), V(0, 1, 0), 18, 1.6, 1.4);
        this.lab.rig.shake = 0.25;
      }
    });
  }

  _nextAction(patient, last) {
    const opts = [];
    if (patient.nextDetached()) opts.push(['attach', 3]);
    if (patient.openWound()) opts.push(['sew', 3], ['organ', 1.4]);
    if (patient.wounds.length < 4) opts.push(['incise', 2]);
    if (patient.wounds.length) opts.push(['organ', 0.6], ['sew', 0.8]);
    opts.push(['inject', 1]);
    const pool = opts.filter(([k]) => k !== last);
    const list = pool.length ? pool : opts;
    const total = list.reduce((s, o) => s + o[1], 0);
    let r = Math.random() * total;
    for (const [k, w] of list) if ((r -= w) <= 0) return k;
    return list[0][0];
  }

  _frameAction(kind, action) {
    const close = { incise: 0.62, sew: 0.7, inject: 0.75, attach: 1.05, organ: 1.15 }[kind] ?? 0.85;
    const dir = kind === 'organ' || kind === 'attach' ? V(0.35, 0.32, 1) : V(0.5, 0.26, 1);
    const site = action.site;
    let smooth = site().clone();
    this._shot({
      target: () => smooth.lerp(site(), 0.08),
      dir, fitH: close, fov: 28,
    }, { speed: 2.0 });
  }

  // 5–7. Zap it alive, reveal, hand the monster to the creature layer.
  async _finish(op, e) {
    const A = this.A;
    const patient = op.patient;
    this._shot('zap', { speed: 2.2 });
    await this._play(A.zap(patient), e);

    this._shot('reveal', { speed: 2.0 });
    const rev = (this.reveal = { patient, t: 0, excite: 0, standing: 0 });
    this.op = null; // a new prompt may start while the monster leaves
    this.sci.play(A.celebrate(1.4));
    const lying = patient.lyingTransform(), standing = patient.standingTransform();
    this.sfx.play('roar');
    await this._w(this._tween(this.hurry ? 0.3 : 0.9, (u) => {
      const k = u * u * (3 - 2 * u);
      patient.holder.position.lerpVectors(lying.pos, standing.pos, k).add(V(0, Math.sin(k * Math.PI) * 0.25, 0));
      patient.holder.quaternion.slerpQuaternions(lying.quat, standing.quat, k);
      rev.excite = 1;
    }), e);
    await this._sleep(this.hurry ? 0.15 : 0.8, e);

    // Leap off the table toward the camera, then hand off to the 2D layer.
    const cam = this.lab.camera;
    const start = patient.holder.position.clone();
    const fwd = V(0, 0, -1).applyQuaternion(cam.quaternion);
    const end = cam.position.clone().addScaledVector(fwd, 1.2).add(V(0.3, -0.9, 0));
    this.sfx.play('squeak');
    await this._w(this._tween(this.hurry ? 0.25 : 0.5, (u) => {
      patient.holder.position.lerpVectors(start, end, u).add(V(0, Math.sin(u * Math.PI) * 0.5, 0));
      patient.holder.rotation.x = u * 0.4;
    }), e);
    this._handoff(op, patient);
    this.reveal = null;

    await this._walkHome(e);
  }

  _handoff(op, patient) {
    const rect = this.getStageRect();
    const cam = this.lab.camera;
    const m = patient.m;
    m.root.updateMatrixWorld(true);
    const feet = m.root.getWorldPosition(V());
    const top = feet.clone().add(V(0, m.height, 0));
    const toScreen = (p) => {
      const n = p.clone().project(cam);
      return { x: rect.x + (n.x * 0.5 + 0.5) * rect.w, y: rect.y + (-n.y * 0.5 + 0.5) * rect.h };
    };
    const a = toScreen(feet), b = toScreen(top);
    const spx = THREE.MathUtils.clamp(Math.abs(a.y - b.y) / m.height, 40, 500);
    patient.dispose();
    const id = op.monsterId;
    const rec = { id, seed: op.seed, defId: op.def.id, theme: op.def.theme, name: themeLabel(op.def) };
    this.creatures.spawnFromStage({ ...rec, sx: a.x, sy: Math.min(a.y, rect.y + rect.h + 40), spx });
    this.onMonsterBorn?.(rec);
    op.born = true;
    if (op.onBorn) setTimeout(op.onBorn, 1300);
  }

  async _scrap(op, e) {
    this._setHurry(false);
    this._shot('wide', { speed: 2.4 });
    await this._play(this.A.scrap(op.patient, { onBinned: () => op.patient.dispose() }), e);
    await this._walkHome(e);
  }

  async _walkHome(e) {
    this._shot('wide', { speed: 2.2 });
    if (this._pending && !this._pending.aborted) return; // next operation is waiting
    await this._play(this.A.walk(this.sci.pose.rootPos.clone(), SPOTS.idle, 0), e);
    this.where = 'idle';
    this.lab.fx.clearSplats();
    this.lab.clearTossed();
    this._shot('idle', { speed: 1.8 });
    this.sci.play(this.A.idle());
  }

  _afterOp(e) {
    if (epoch_ok(e, this)) this.maybeCleanup();
  }

  // ------------------------------------------------------------- cleanup
  maybeCleanup() {
    if (this.cleaning) return;
    if (this.canCleanup && !this.canCleanup()) return;
    if (this.creatures.count() < this.getThreshold()) return;
    this.cleaning = true;
    this.onCleanupStart?.();
    this._run((e) => this._cleanup(e));
  }

  async _cleanup(e) {
    const A = this.A;
    const lab = this.lab;
    this._thrown = [];
    this._shot('cleanup', { speed: 2.2 });
    const here = this.sci.pose.rootPos.clone();
    await this._play(A.walk(here, A.AT, 0), e);
    this.where = 'cleanup';

    // 1–2. Bomb, take cover, boom.
    let landed;
    const bombArrived = new Promise((r) => { landed = r; });
    await this._play(A.throwThing(P.bomb, {
      onRelease: (prop) => {
        this._thrown.push(prop);
        this._throwAtCamera(prop, 0.6, () => {
          prop.parent?.remove(prop);
          this.overlay.flash('255,230,170', 0.55);
          lab.rig.shake = 1;
          this.sfx.play('boom');
          landed(this.creatures.explodeAll());
        });
      },
    }), e);
    this.sci.play(A.cower());
    const boom = await this._w(bombArrived, e);
    await this._w(boom, e);
    this.onCreaturesChanged?.();

    // 3. Acid.
    let splashed;
    const acidArrived = new Promise((r) => { splashed = r; });
    await this._play(A.throwThing(P.flask, {
      release: 1.5, dur: 2.0, sound: 'whoosh',
      onRelease: (prop) => {
        this._thrown.push(prop);
        this._throwAtCamera(prop, 0.5, () => {
          prop.parent?.remove(prop);
          this.overlay.splat(this.getStageRect(), '120,255,60', 9);
          this.sfx.play('splat');
          this.sfx.play('sizzle');
          splashed(this.creatures.dissolveAll());
        });
      },
    }), e);
    this.sci.play(A.celebrate(1.2, A.AT));
    const melt = await this._w(acidArrived, e);
    await this._w(melt, e);

    // 4. Hose the screen top to bottom.
    const hose = (this._hoseAction = A.hose());
    this.sci.play(hose);
    await this._sleep(0.5, e);
    await this._w(this.overlay.wash(2.0, (y) => this.creatures.washTo(y)), e);
    hose.stop();
    this._hoseAction = null;

    // 5. Clear and return to idle.
    this.creatures.clear();
    this.onCreaturesChanged?.();
    lab.fx.clearSplats();
    lab.clearTossed();
    this.cleaning = false;
    this.onCleanupDone?.({ cancelled: false });
    if (this._pending && !this._pending.aborted) return;
    await this._play(A.walk(A.AT, SPOTS.idle, 0), e);
    this.where = 'idle';
    this._shot('idle', { speed: 1.8 });
    this.sci.play(A.idle());
  }

  _throwAtCamera(prop, dur, onArrive) {
    const cam = this.lab.camera;
    const from = prop.position.clone();
    const fwd = V(0, 0, -1).applyQuaternion(cam.quaternion);
    const to = cam.position.clone().addScaledVector(fwd, 0.25);
    this._tween(dur, (u) => {
      prop.position.lerpVectors(from, to, u * u).add(V(0, Math.sin(u * Math.PI) * 0.35, 0));
      prop.rotation.x += 0.3; prop.rotation.z += 0.2;
      if (prop.userData.spark) prop.userData.spark.scale.setScalar(0.6 + Math.random());
    }).then(onArrive);
  }

  // ---------------------------------------------------------------- frame
  update(dt) {
    const k = this.hurry ? 2.4 : 1;
    this.tweens = this.tweens.filter((tw) => {
      tw.t += dt * k;
      const u = Math.min(1, tw.t / tw.dur);
      tw.fn(u);
      if (u >= 1) { tw.resolve(); return false; }
      return true;
    });
    const rev = this.reveal;
    if (rev) {
      rev.t += dt;
      animateMonster(rev.patient.m, rev.t, { excite: rev.excite, walk: 0 });
      rev.excite = Math.max(0.3, rev.excite - dt * 0.5);
    }
  }
}

function epoch_ok(e, d) { return e === d.epoch; }

const themeLabel = (def) => themeOf(def.theme).label;
