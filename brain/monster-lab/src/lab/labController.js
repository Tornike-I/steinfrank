// Laboratory controller. Every prompt describes a job, and each topic has
// exactly one monster (registry.topicForJob):
//   - new topic   → Frankenstein forges a monster for the topic (surgery
//                   cinematic) and its tab opens. The topic picks the look
//                   and voice; the tab is named after the topic.
//   - known topic → that topic's monster learns the job (Frankenstein
//                   re-forges it in the background to cover it), charges the
//                   screen screaming, and its existing chat opens. The
//                   prompt itself is never sent to the monster.
// Prompts submitted during cleanup wait until the hose is done.
import { state, save } from '../core/store.js';
import { draftAssistant, commitAssistant, allMonsters, titleOf, topicForJob, learnJob, topicLabelOf } from '../monsters/registry.js';
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

// One assistant per topic, covering every job it has been given so far.
function forgeBrief(def) {
  const topic = topicLabelOf(def).toLowerCase();
  const jobs = (def.jobs?.length ? def.jobs : [def.job]).filter(Boolean).map((j) => `- ${j.replace(/"/g, "'").slice(0, 300)}`).join('\n');
  return `A ${topic} assistant monster: the one assistant for everything about ${topic}. It is run again and again, each time with whatever the user asks or wants done about ${topic}.
Inputs: exactly one required string input named "question" (the user's request about ${topic}, maxLength 500).
It must handle these jobs well (more get added over time):
${jobs}
Gather facts with free limbs (web_search, http_fetch) where they help, then compose a short spoken answer and a markdown report.
Stay on the topic of ${topic}; politely decline anything else.
Voice archetype: ${VOICE_FOR_TOPIC[def.theme] || 'brute'}.${isCzech(jobs) ? ' The user writes in Czech: the monster speaks Czech (voice.language "cs").' : ''}`;
}

// Forge and publish a topic monster from Frankenstein; returns the provider.
async function forgeTopicMonster(def, signal) {
  const res = await F.forge(forgeBrief(def), signal);
  if (res.status === 'refused') throw new Error(`Frankenstein refused: ${res.reason}`);
  if (res.status !== 'draft') throw new Error(`Frankenstein couldn't design it: ${(res.errors || []).slice(0, 2).join('; ') || res.status}`);
  const card = await F.publish(res.spec.id, WITH_VOICE);
  return {
    provider: { kind: 'frankenstein', monsterId: card.id, name: card.name, purpose: card.purpose, inputs: card.inputs, voice: card.has_voice },
    birthUrl: card.voice?.birth_url || null,
    limbs: card.limbs,
  };
}

// Publish with an ElevenLabs voice agent, sounds and birth scene? VITE_FRANK_VOICE=0 turns it off.
const WITH_VOICE = import.meta.env.VITE_FRANK_VOICE !== '0';

export class LabController {
  constructor({ director, creatures, ui, monsters }) {
    this.director = director;
    this.learning = new Map(); // monster id → { again } while a re-forge runs
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
    const found = topicForJob(job);
    const topic = found.theme;
    const existing = found.existing;
    if (existing) {
      const learned = learnJob(existing, job);
      if (learned) {
        existing.chat ||= [];
        existing.chat.push({ id: `note-${Date.now().toString(36)}`, role: 'note', content: `Learned a new job: ${job}` });
        this._relearn(existing);
      }
      this.busy = { kind: 'summon', topic };
      this.ui.banner(`<b>${escapeHtml(titleOf(existing))}</b> already exists${learned ? ' — it is learning this job' : ''}`, { busy: true });
      this.ui.renderControls();
      await this.director.summon(existing, { into: () => this._into(existing.id) });
      this.busy = null;
      this.ui.banner(null);
      this.ui.renderControls();
      return;
    }
    const draft = draftAssistant(topic, job, found.topic);
    const forgeAbort = new AbortController();
    this.busy = { kind: 'create', topic, draft, forgeAbort };
    const real = F.backend.connected;
    this.ui.banner(`Creating a <b>${escapeHtml(found.label)}</b> monster${real ? ' — Frankenstein is designing it…' : ''}`, { busy: true });
    this.ui.renderControls();

    // Limbs come from Frankenstein's design (or the scripted workflow offline).
    const limbs = real
      ? (async () => {
        const made = await forgeTopicMonster(draft, forgeAbort.signal);
        draft.provider = made.provider;
        draft.birthUrl = made.birthUrl;
        draft.limbs = made.limbs;
        return made.limbs;
      })()
      : Promise.resolve(draft.limbs);
    limbs.then((names) => {
      if (this.busy?.draft !== draft) return;
      const list = [...new Set(names.map(limbLabel))];
      this.ui.banner(`Creating a <b>${escapeHtml(found.label)}</b> monster — sewing on ${list.length} limb${list.length === 1 ? '' : 's'}: ${list.join(', ')}`, { busy: true });
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

  // From the summon close-up into the monster's room: dip the stage to
  // black, switch tabs while it's dark, and fade the room in with its camera
  // pulling back from the face.
  async _into(id) {
    await this.ui.fadeStage(1, 380);
    this.ui.openMonster(id);
    this.ui.mon?.den?.enter();
    this.ui.arrive();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    this.ui.fadeStage(0, 750);
  }

  // A forged monster that learned a job is re-forged in the background to
  // cover all its jobs; it keeps answering with the old version until the
  // new one is published. Offline (scripted) monsters already answer the
  // whole topic, so they only record the job.
  async _relearn(def) {
    if (def.provider?.kind !== 'frankenstein' || !F.backend.connected) return;
    const busy = this.learning.get(def.id);
    if (busy) { busy.again = true; return; }
    const entry = { again: false };
    this.learning.set(def.id, entry);
    try {
      do {
        entry.again = false;
        const made = await forgeTopicMonster(def);
        def.provider = made.provider;
        def.limbs = [...new Set([...(def.limbs || []), ...(made.limbs || [])])];
        save();
      } while (entry.again);
      this._note(def, `Finished learning — now covers ${def.jobs.length} jobs.`);
    } catch (err) {
      this._note(def, `Couldn't learn the new job yet (${err?.message || 'forge failed'}); answering as before.`);
    } finally {
      this.learning.delete(def.id);
    }
  }

  _note(def, content) {
    def.chat ||= [];
    def.chat.push({ id: `note-${Date.now().toString(36)}`, role: 'note', content });
    save();
    this.ui.renderAll();
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
