// The Director turns lifecycle events into choreography.
//
//   create(def, limbs) – build a topic monster: the surgery keeps going until
//                        Frankenstein has designed it (`limbs` resolves with
//                        the spec's limb names), each limb is then delivered
//                        and sewn on, lightning, and the monster leaps off the
//                        slab onto the lab floor.
//   summon(def)        – an existing monster charges the camera and screams.
//   abortCreate()      – the unfinished body is scrapped into the bin.
//   maybeCleanup()     – overcrowding: bomb, acid, hose (counterparts only).
//
// All sequences run on one serial chain; reset() cancels everything.
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
// The operating table's slab, as a solid for the scientist's arms.
const TABLE_BOX = { kind: 'box', min: V(SPOTS.table.x - 1.02, 0, SPOTS.table.z - 0.39), max: V(SPOTS.table.x + 1.02, SPOTS.tableTop + 0.01, SPOTS.table.z + 0.39) };

const SHOTS = {
  idle: { target: V(0, 1.42, 0), dir: V(0.2, 0.05, 1), fitH: 1.35, fitW: 1.9, fov: 30 },
  wide: { target: V(1.0, 1.15, 0.0), dir: V(0.12, 0.22, 1), fitH: 1.9, fitW: 3.4, fov: 32 },
  zap: { target: V(1.85, 1.4, -0.3), dir: V(-0.25, 0.15, 1), fitH: 1.6, fitW: 2.6, fov: 30 },
  reveal: { target: V(1.5, 1.4, 0.1), dir: V(0, 0.08, 1), fitH: 1.55, fitW: 2.0, fov: 30 },
  // Pulled back so the floor in front of the scientist (where monsters roam) is in view.
  floor: { target: V(0.55, 0.85, 0.75), dir: V(0.08, 0.34, 1), fitH: 2.5, fitW: 5.6, fov: 32 },
  cleanup: { target: V(0.55, 0.8, 0.9), dir: V(0.05, 0.4, 1), fitH: 2.7, fitW: 6.0, fov: 32 },
  // Low, eye-level framing for a monster charging at the viewer.
  summon: { target: V(0.55, 0.9, 2.2), dir: V(0.04, 0.1, 1), fitH: 2.2, fov: 32 },
};

export class Director {
  constructor({ lab, creatures, overlay, sfx, getStageRect, getThreshold, canCleanup, onMonsterBorn, onCreaturesChanged, onCleanupStart, onCleanupDone }) {
    Object.assign(this, { lab, creatures, overlay, sfx, getStageRect, getThreshold, canCleanup, onMonsterBorn, onCreaturesChanged, onCleanupStart, onCleanupDone });
    this.sci = lab.scientist;
    this.sci.obstacles = [TABLE_BOX];
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
  create(def, limbs = Promise.resolve(def.limbs || [])) {
    return new Promise((resolve, reject) => {
      const op = this.begin({ messageId: `create-${def.id}`, def });
      op.minActions = 2;
      op.limbQueue = [];
      op.waitingLimbs = true;
      op.monsterId = `w-${def.id}-${Date.now().toString(36)}`;
      op.onBorn = resolve;
      op.onAbort = (err) => reject(err || new Error('cancelled'));
      this._createOp = op;
      Promise.resolve(limbs).then((names) => {
        op.limbQueue.push(...(names || []));
        op.def = { ...op.def, limbs: names || [] };
        op.waitingLimbs = false;
        op.done = true;
      }, (err) => {
        op.failure = err;
        this.abort({ messageId: op.messageId });
      });
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
    this._shot('summon', { speed: 2.6 });
    await this.creatures.summon(
      { seed: def.seed, theme: def.theme, defId: def.id, name: themeLabel(def), limbs: def.limbs || [] },
      {
        onScream: () => {
          this.sfx.play('scream');
          this.lab.rig.shake = 0.6;
        },
        target: V(0.55, 0, 3.75),
      },
    );
    if (this.where === 'idle' && !this.op) sci.play(this.A.idle());
    if (!this.op && !this.cleaning) this._shot(this._idleShot(), { speed: 1.8 });
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
    this.lab.water.stop();
    this.lab.water.active = false;
    this.lab.water.film.visible = false;
    this.lab.zapLight.intensity = 0;
    this.lab.lever.rotation.x = -0.6;
    this.lab.binLid.rotation.x = -1.2;
    const wasCleaning = this.cleaning;
    this.cleaning = false;
    this._setHurry(false);
    this.where = 'idle';
    this.sci.obstacles = [TABLE_BOX];
    this.sci.play(this.A.idle());
    this.sci._prev = this.sci.pose; // no blend from wherever he was
    this._shot(this._idleShot(), { cut: true });
    if (wasCleaning) this.onCleanupDone?.({ cancelled: true });
  }

  // ------------------------------------------------------------- surgery
  async _operate(op, e) {
    this._pending = null;
    if (op.aborted) { this._setHurry(false); op.onAbort?.(op.failure); return; } // stopped before we even started
    this.op = op;
    this._setHurry(false);
    const A = this.A;
    op.patient = new Patient(op.def, this.lab.scene);
    this._dropIn(op.patient);
    // Solid things his hands and arms must not pass through.
    this.sci.obstacles = [TABLE_BOX, { kind: 'ellipsoid', obj: op.patient.m.torso, rad: op.patient.m.torsoRadii }];

    // 1–2. Start working; pull back to reveal the slab.
    this._shot('wide', { speed: 2.4 });
    if (this.where === 'idle') {
      await this._play(A.snap(), e);
      await this._play(A.walk(SPOTS.idle, SPOTS.work, 0), e);
    } else {
      await this._play(A.walk(this.sci.pose.rootPos.clone(), SPOTS.work, 0), e);
    }
    this.where = 'work';

    // 3–4. Operate until Frankenstein has designed the monster; then each of
    // its limbs is delivered and sewn on.
    let last = null;
    let first = true;
    let count = 0;
    for (;;) {
      if (op.aborted) break;
      let kind;
      if (!first && op.limbQueue?.length) {
        await this._w(this._deliverLimb(op.patient, op.limbQueue.shift()), e);
        if (op.aborted) break;
        kind = 'attach';
      } else if (first) kind = 'incise';
      // Design is back: sew on whatever is still loose, then finish.
      else if (op.done && op.patient.nextDetached()) kind = 'attach';
      else if (op.done && count >= (op.minActions || 1)) break;
      // Still waiting on Frankenstein: keep operating.
      else kind = this._nextAction(op.patient, last);
      count++;
      const action = A[kind](op.patient);
      this._frameAction(kind, action);
      await this._play(action, e);
      last = kind;
      first = false;
    }

    if (op.aborted) { await this._scrap(op, e); op.onAbort?.(op.failure); }
    else await this._finish(op, e);
    this.op = null;
    this.sci.obstacles = [TABLE_BOX];
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

  // A limb drops from the rafters onto the slab's front edge.
  _deliverLimb(patient, name) {
    const d = patient.deliverLimb(name);
    if (!d) return Promise.resolve();
    // Frame the landing spot and the socket it is headed for.
    const sock = patient.socketWorld(d).pos;
    const spot = d.obj.position.clone();
    this._shot({ target: spot.clone().lerp(sock, 0.6).add(V(0, 0.15, 0)), dir: V(0.2, 0.75, 1), fitH: 1.35, fov: 28 }, { speed: 2.2 });
    const y = d.obj.position.y;
    d.obj.position.y += 2.2;
    this.sfx.play('whoosh');
    return this._tween(0.5, (u) => {
      const f = Math.min(1, (u / 0.8) ** 2);
      d.obj.position.y = y + 2.2 * (1 - f) + (u > 0.8 ? Math.sin(((u - 0.8) / 0.2) * Math.PI) * 0.04 : 0);
      if (u >= 0.8 && !d._landed) { d._landed = true; this.sfx.play('splat'); this.lab.fx.blood(d.obj.position.clone(), V(0, 1, 0), 8); }
    });
  }

  _idleShot() { return this.creatures.count() > 0 ? 'floor' : 'idle'; }

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
    const close = { incise: 0.62, sew: 0.7, inject: 0.75, attach: 1.25, organ: 1.15 }[kind] ?? 0.85;
    // Attaching: higher, so the hands coming over the body to the socket are
    // in view rather than hidden behind the patient.
    const dir = kind === 'attach' ? V(0.2, 0.75, 1) : kind === 'organ' ? V(0.35, 0.32, 1) : V(0.5, 0.26, 1);
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

    // Leap off the slab onto the lab floor and join the others.
    this._shot('floor', { speed: 2.0 });
    this.sfx.play('squeak');
    this.reveal = null;
    const rec = { id: op.monsterId, seed: op.seed, defId: op.def.id, theme: op.def.theme, name: themeLabel(op.def), limbs: op.def.limbs || [] };
    this.sci.obstacles = [TABLE_BOX]; // the monster is leaving the slab
    const landed = this.creatures.adopt(rec, patient);
    this.onMonsterBorn?.(rec);
    op.born = true;
    await this._w(landed, e);
    if (op.onBorn) setTimeout(op.onBorn, 900);
    await this._walkHome(e);
  }

  async _scrap(op, e) {
    this._setHurry(false);
    this._shot('wide', { speed: 2.4 });
    await this._play(this.A.scrap(op.patient, { onBinned: () => op.patient.dispose() }), e);
    await this._walkHome(e);
  }

  async _walkHome(e) {
    if (!this.creatures.count()) this._shot('wide', { speed: 2.2 });
    if (this._pending && !this._pending.aborted) return; // next operation is waiting
    await this._play(this.A.walk(this.sci.pose.rootPos.clone(), SPOTS.idle, 0), e);
    this.where = 'idle';
    this.lab.fx.clearSplats();
    this.lab.clearTossed();
    this._shot(this._idleShot(), { speed: 1.8 });
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
        const at = this._crowdCentre();
        this._throwTo(prop, at, 0.8, () => {
          prop.parent?.remove(prop);
          this.overlay.flash('255,230,170', 0.4);
          lab.rig.shake = 1;
          this.sfx.play('boom');
          landed(this.creatures.explodeAt(at));
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
        const at = this._crowdCentre();
        this._throwTo(prop, at, 0.7, () => {
          prop.parent?.remove(prop);
          this.lab.fx.goo(at.clone().setY(0.1), 40, 2.5);
          this.sfx.play('splat');
          this.sfx.play('sizzle');
          splashed(this.creatures.dissolveAll());
        });
      },
    }), e);
    this.sci.play(A.celebrate(1.2, A.AT));
    const melt = await this._w(acidArrived, e);
    await this._w(melt, e);

    // 4. Hose down the floor, back of the room to the front; the water runs
    // toward the viewer and carries the mess off the bottom of the screen.
    lab.water.start(this.creatures);
    const hose = (this._hoseAction = A.hose());
    this.sci.play(hose);
    await this._sleep(1.2 + 4.2, e); // pick up, then sweep side to side
    hose.stop();
    lab.water.stop();
    this.sci.play(A.celebrate(1.2, A.AT));
    await this._sleep(1.6, e);
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
    this._shot(this._idleShot(), { speed: 1.8 });
    this.sci.play(A.idle());
  }

  // Where to aim the bomb / acid: the middle of the crowd on the floor.
  _crowdCentre() {
    const list = [...this.creatures.creatures.values()];
    if (!list.length) return V(0.8, 0, 1.4);
    const c = list.reduce((a, x) => a.add(x.pos), V()).multiplyScalar(1 / list.length);
    return c.setY(0);
  }

  _throwTo(prop, to, dur, onArrive) {
    const from = prop.position.clone();
    const arc = 1.0 + from.distanceTo(to) * 0.25;
    this._tween(dur, (u) => {
      prop.position.lerpVectors(from, to, u).add(V(0, Math.sin(u * Math.PI) * arc, 0));
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
