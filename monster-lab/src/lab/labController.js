// Laboratory controller. A lab question is routed to a topic:
//   - new topic  → the scientist builds that topic's monster (short
//                  cinematic), its tab opens and the question is answered there
//   - known topic → the existing monster charges the screen and screams,
//                  then its tab opens with the question appended
// Prompts submitted during cleanup wait until the hose is done.
import { state, save } from '../core/store.js';
import { matchTheme, themeOf } from '../monsters/themes.js';
import { findByTopic, draftAssistant, commitAssistant } from '../monsters/registry.js';

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
    this.busy = { kind: 'create', topic, draft };
    this.ui.banner(`Creating a <b>${th.label}</b> monster`, { busy: true });
    this.ui.renderControls();
    try {
      await this.director.create(draft);
    } catch {
      this.busy = null;
      this.ui.banner('Creation cancelled — the specimen went in the bin.', { timeout: 2600 });
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
    if (this.busy?.kind === 'create') this.director.abortCreate();
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
