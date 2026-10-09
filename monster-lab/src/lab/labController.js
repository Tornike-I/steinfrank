// Laboratory controller. Every prompt describes a job; its topic is detected:
//   - new topic   → Frankenstein forges a monster for it (surgery cinematic)
//                   and its tab opens. The topic picks the look and voice.
//   - known topic → that topic's existing monster charges the screen and
//                   screams, then its existing chat opens.
// Prompts submitted during cleanup wait until the hose is done.
import { state, save } from '../core/store.js';
import { matchTheme, themeOf } from '../monsters/themes.js';
import { draftAssistant, commitAssistant, allMonsters, titleOf } from '../monsters/registry.js';
import { limbLabel } from '../monsters/limbParts.js';
import * as F from '../ai/frankenstein.js';
import { monsterVoice } from '../audio/monsterVoice.js';

// Each topic always gets the same kind of voice (archetypes: frankenstein/voices.json).
const VOICE_FOR_TOPIC = {
  weather: 'golem', meetings: 'butler', research: 'lich', writing: 'igor', math: 'gremlin', cooking: 'hag',
  travel: 'brute', health: 'brute', music: 'igor', tech: 'swarm', history: 'lich', space: 'golem', money: 'gremlin',
  sports: 'brute', movies: 'butler', animals: 'swarm', gardening: 'hag', language: 'butler', love: 'hag',
  games: 'gremlin', generic: 'brute',
};
const isCzech = (text) => /[ěščřžůťďň]/i.test(text);

function forgeBrief(th, job) {
  return `Build a monster for this job, described by the user: "${job.replace(/"/g, "'").slice(0, 600)}"
It will be run again and again for exactly this job. Give it the few simple inputs the job needs, each with a clear description.
Voice archetype: ${VOICE_FOR_TOPIC[th.id] || 'brute'}.${isCzech(job) ? ' The user writes in Czech: the monster speaks Czech (voice.language "cs").' : ''}`;
}

// Publish with an ElevenLabs voice agent, sounds and birth scene? VITE_FRANK_VOICE=0 turns it off.
const WITH_VOICE = import.meta.env.VITE_FRANK_VOICE !== '0';

export class LabController {
  constructor({ director, creatures, ui, monsters }) {
    this.director = director;
    this.creatures = creatures;
    this.ui = ui;
    this.monsters = monsters;
    this.busy = null; // { kind: 'create' | 'summon', topic }
    this.queued = null; // job waiting for cleanup to finish
  }

  get generating() { return !!this.busy || !!this.queued; }
  get conv() { return null; } // the lab shows no transcript

  init() {
    this.creatures.restore(state.labCreatures || []);
    this.creatures.sync(allMonsters());
    setInterval(() => this.persistCreatures(), 2000);
    setTimeout(() => { if (state.mode === 'lab') this.director.maybeCleanup(); }, 1500);
  }

  persistCreatures() {
    state.labCreatures = this.creatures.serialize();
    save();
  }

  send(job) {
    job = job.trim();
    if (!job || this.generating) return false;
    if (this.director.cleaning) {
      this.queued = job;
      this.ui.banner('Queued — the lab is being hosed down first…', { busy: true });
      this.ui.renderControls();
      return true;
    }
    this._start(job);
    return true;
  }

  async _start(job) {
    const topic = matchTheme(job);
    const th = themeOf(topic);
    const existing = this._existingFor(topic);
    if (existing) {
      this.busy = { kind: 'summon', topic };
      this.ui.banner(`Summoning <b>${escapeHtml(titleOf(existing))}</b>`, { busy: true });
      this.ui.renderControls();
      await this.director.summon(existing);
      this.busy = null;
      this.ui.banner(null);
      this.ui.renderControls();
      this.ui.openMonster(existing.id);
      return;
    }
    const draft = draftAssistant(topic, job);
    const forgeAbort = new AbortController();
    this.busy = { kind: 'create', topic, draft, forgeAbort };
    const real = F.backend.connected;
    this.ui.banner(`Building a monster for: <b>${escapeHtml(job.slice(0, 90))}</b>${real ? ' — Frankenstein is designing it…' : ''}`, { busy: true });
    this.ui.renderControls();

    // Limbs come from Frankenstein's design (or the scripted workflow offline).
    const limbs = real
      ? (async () => {
        const res = await F.forge(forgeBrief(th, job), forgeAbort.signal);
        if (res.status === 'refused') throw new Error(`Frankenstein refused: ${res.reason}`);
        if (res.status !== 'draft') throw new Error(`Frankenstein couldn't design it: ${(res.errors || []).slice(0, 2).join('; ') || res.status}`);
        const card = await F.publish(res.spec.id, WITH_VOICE);
        draft.provider = { kind: 'frankenstein', monsterId: card.id, name: card.name, purpose: card.purpose, inputs: card.inputs, voice: card.has_voice };
        draft.birthUrl = card.voice?.birth_url || null;
        draft.title = card.name;
        draft.limbs = card.limbs;
        return card.limbs;
      })()
      : Promise.resolve(draft.limbs);
    limbs.then((names) => {
      if (this.busy?.draft !== draft) return;
      const list = [...new Set(names.map(limbLabel))];
      this.ui.banner(`Building <b>${escapeHtml(draft.title || th.label + ' monster')}</b> — sewing on ${list.length} limb${list.length === 1 ? '' : 's'}: ${list.join(', ')}`, { busy: true });
    }, () => {});

    try {
      await this.director.create(draft, limbs);
    } catch (err) {
      this.busy = null;
      const why = err?.message && err.message !== 'cancelled' && err.name !== 'AbortError' ? err.message : 'Creation cancelled';
      this.ui.banner(`${escapeHtml(why)} — the specimen went in the bin.`, { timeout: why === 'Creation cancelled' ? 2600 : 9000 });
      this.ui.renderControls();
      return;
    }
    monsterVoice.scene(draft.birthUrl);
    const a = commitAssistant(draft);
    this.busy = null;
    this.persistCreatures();
    this.ui.banner(null);
    this.ui.renderControls();
    this.ui.openMonster(a.id);
  }

  // The topic's most recent monster. Unrecognised ("generic") jobs always get
  // a new monster, since they have nothing in common with each other.
  _existingFor(topic) {
    if (topic === 'generic') return null;
    return allMonsters().filter((m) => m.theme === topic).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))[0] || null;
  }

  stop() {
    if (this.queued) {
      this.queued = null;
      this.ui.banner(null);
    }
    if (this.busy?.kind === 'create') {
      this.busy.forgeAbort?.abort();
      this.director.abortCreate();
    }
    this.ui.renderControls();
  }

  onCleanupDone() {
    this.creatures.sync(allMonsters());
    this.persistCreatures();
    const q = this.queued;
    this.queued = null;
    this.ui.renderControls();
    if (q) this._start(q);
  }
}

const escapeHtml = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
