// Monster controller: the chat inside one topic assistant's tab. Each prompt
// goes through the assistant's provider (scripted for now); workflow step
// events drive the Den performance and appear as a live checklist, then the
// answer streams in. Stop/fail just returns the monster to idle; regenerate
// reuses the same assistant. Several assistants may answer at once.
import { state, save } from '../core/store.js';
import { uid } from '../core/rng.js';
import { getMonster, allMonsters } from '../monsters/registry.js';
import { respond } from '../ai/assistants.js';

export class MonsterController {
  constructor({ den, ui }) {
    this.den = den;
    this.ui = ui;
    this.gens = new Map(); // assistant id → { msgId, ctrl }
  }

  get def() {
    return getMonster(state.selectedMonster) || allMonsters()[0] || null;
  }
  get conv() {
    const d = this.def;
    if (!d) return null;
    d.chat ||= [];
    return { id: d.id, messages: d.chat };
  }
  get generating() { return !!this.def && this.gens.has(this.def.id); }

  select(id) {
    state.selectedMonster = id;
    save();
    this.den.setMonster(this.def);
  }

  // Open a tab and ask in one go (used by the laboratory).
  ask(id, text) {
    this.select(id);
    this.ui.renderAll();
    this.send(text);
  }

  send(text) {
    text = text.trim();
    const def = this.def;
    if (!text || !def || this.generating) return false;
    def.chat ||= [];
    def.chat.push({ id: uid('u'), role: 'user', content: text });
    const msg = { id: uid('a'), role: 'assistant', content: '', status: 'pending', steps: [] };
    def.chat.push(msg);
    save();
    this.ui.renderAll();
    this._run(def, msg, text);
    return true;
  }

  regenerate(msgId) {
    const def = this.def;
    if (!def || this.generating) return;
    const msg = def.chat.find((m) => m.id === msgId);
    if (!msg || msg !== def.chat[def.chat.length - 1]) return;
    const prompt = def.chat[def.chat.length - 2]?.content || '';
    Object.assign(msg, { content: '', status: 'pending', steps: [], error: null });
    save();
    this.ui.renderAll();
    this._run(def, msg, prompt);
  }

  async _run(def, msg, input) {
    const ctrl = new AbortController();
    this.gens.set(def.id, { msgId: msg.id, ctrl });
    msg.status = 'streaming';
    this.ui.updateMessage(msg);
    this.ui.renderControls();
    const live = () => state.mode === 'monsters' && this.den.def?.id === def.id;
    const history = def.chat.slice(0, -1).map(({ role, content }) => ({ role, content }));
    try {
      for await (const ev of respond(def, input, { signal: ctrl.signal, history })) {
        if (ev.type === 'step') {
          msg.steps.push({ id: ev.step.id, label: ev.step.label, kind: ev.step.kind, state: 'running' });
          if (live()) this.den.onStep(ev.step);
        } else if (ev.type === 'stepDone') {
          const s = msg.steps.find((x) => x.id === ev.step.id);
          if (s) s.state = 'done';
        } else if (ev.type === 'token') {
          msg.content += ev.text;
          if (live()) this.den.onToken();
        }
        this.ui.updateMessage(msg);
      }
      msg.status = 'complete';
      if (live()) this.den.onDone();
    } catch (err) {
      const stopped = err.name === 'AbortError';
      msg.status = stopped ? 'stopped' : 'error';
      if (!stopped) msg.error = err.message;
      for (const s of msg.steps) if (s.state === 'running') s.state = 'cancelled';
      if (live()) this.den.onFail(stopped);
    } finally {
      if (this.gens.get(def.id)?.msgId === msg.id) this.gens.delete(def.id);
      save();
      this.ui.updateMessage(msg);
      this.ui.renderControls();
    }
  }

  stop() { if (this.def) this.gens.get(this.def.id)?.ctrl.abort(); }
}
