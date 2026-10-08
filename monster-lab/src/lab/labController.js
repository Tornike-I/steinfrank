// Laboratory controller. A lab question is routed to a topic:
//   - new topic  → the scientist builds that topic's monster (short
//                  cinematic), its tab opens and the question is answered there
//   - known topic → the existing monster charges the screen and screams,
//                  then its tab opens with the question appended
// Prompts submitted during cleanup wait until the hose is done.
import { state, save } from '../core/store.js';
import { matchTheme, themeOf } from '../monsters/themes.js';
import { findByTopic, draftAssistant, commitAssistant } from '../monsters/registry.js';
import { limbLabel } from '../monsters/limbParts.js';
import * as F from '../ai/frankenstein.js';

// What we ask Frankenstein to build for a topic. A single free-text `question`
// input keeps lab questions mappable onto every topic monster.
function forgeBrief(th, question) {
  return `A ${th.label} assistant monster that answers any question a user asks about ${th.label.toLowerCase()} in plain words.
Inputs: exactly one required string input named "question" (the user's question about ${th.label.toLowerCase()}, maxLength 500).
Gather facts with free limbs (web_search, http_fetch) where they help, then compose a short spoken answer and a markdown report.
Stay strictly on the topic of ${th.label.toLowerCase()}; politely decline anything else.
Example question: "${question.replace(/"/g, "'").slice(0, 200)}"`;
}

// Publish with an ElevenLabs voice agent? Needs the Agents permission on the key.
const WITH_VOICE = import.meta.env.VITE_FRANK_VOICE === '1';

export class LabController {
  constructor({ director, creatures, ui, monsters }) {
    this.director = director;
    this.creatures = creatures;
    this.ui = ui;
    this.monsters = monsters;
    this.busy = null; // { kind: 'create' | 'summon', topic }
    this.queued = null; // question waiting for cleanup to finish
  }

  get generating() { return !!this.busy || !!this.queued; }
  get conv() { return null; } // the lab shows no transcript

  init() {
    this.creatures.restore(state.labCreatures || []);
    setInterval(() => this.persistCreatures(), 2000);
    setTimeout(() => { if (state.mode === 'lab') this.director.maybeCleanup(); }, 1500);
  }

  persistCreatures() {
    state.labCreatures = this.creatures.serialize();
    save();
  }

  send(question) {
    question = question.trim();
    if (!question || this.generating) return false;
    const topic = matchTheme(question);
    const label = themeOf(topic).label;
    if (this.director.cleaning) {
      this.queued = question;
      this.ui.banner(`Queued for the <b>${label}</b> monster — the lab is being hosed down…`, { busy: true });
      this.ui.renderControls();
      return true;
    }
    this._start(question);
    return true;
  }

  async _start(question) {
    const topic = matchTheme(question);
    const th = themeOf(topic);
    const existing = findByTopic(topic);
    if (existing) {
      this.busy = { kind: 'summon', topic };
      this.ui.banner(`Summoning the <b>${th.label}</b> monster`, { busy: true });
      this.ui.renderControls();
      await this.director.summon(existing);
      this.busy = null;
      this.ui.banner(null);
      this.ui.renderControls();
      this.ui.openMonster(existing.id);
      this.monsters.ask(existing.id, question);
      return;
    }
    const draft = draftAssistant(topic);
    const forgeAbort = new AbortController();
    this.busy = { kind: 'create', topic, draft, forgeAbort };
    const real = F.backend.connected;
    this.ui.banner(`Creating a <b>${th.label}</b> monster${real ? ' — Frankenstein is designing it…' : ''}`, { busy: true });
    this.ui.renderControls();

    // Limbs come from Frankenstein's design (or the scripted workflow offline).
    const limbs = real
      ? (async () => {
        const res = await F.forge(forgeBrief(th, question), forgeAbort.signal);
        if (res.status === 'refused') throw new Error(`Frankenstein refused: ${res.reason}`);
        if (res.status !== 'draft') throw new Error(`Frankenstein couldn't design it: ${(res.errors || []).slice(0, 2).join('; ') || res.status}`);
        const card = await F.publish(res.spec.id, WITH_VOICE);
        draft.provider = { kind: 'frankenstein', monsterId: card.id, name: card.name, purpose: card.purpose, inputs: card.inputs, voice: card.has_voice };
        draft.limbs = card.limbs;
        return card.limbs;
      })()
      : Promise.resolve(draft.limbs);
    limbs.then((names) => {
      if (this.busy?.draft !== draft) return;
      const list = [...new Set(names.map(limbLabel))];
      this.ui.banner(`Creating a <b>${th.label}</b> monster — sewing on ${list.length} limb${list.length === 1 ? '' : 's'}: ${list.join(', ')}`, { busy: true });
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
    const a = commitAssistant(draft);
    this.busy = null;
    this.persistCreatures();
    this.ui.banner(null);
    this.ui.renderControls();
    this.ui.openMonster(a.id);
    this.monsters.ask(a.id, question);
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
    this.persistCreatures();
    const q = this.queued;
    this.queued = null;
    this.ui.renderControls();
    if (q) this._start(q);
  }
}

const escapeHtml = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
