// Monster controller: the chat inside one topic assistant's tab. Each prompt
// goes through the assistant's provider (scripted for now); workflow step
// events drive the Den performance and appear as a live checklist, then the
// answer streams in. Stop/fail just returns the monster to idle; regenerate
// reuses the same assistant. Several assistants may answer at once.
import { state, save } from '../core/store.js';
import { uid } from '../core/rng.js';
import { getMonster, allMonsters } from '../monsters/registry.js';
import { respond, frankensteinSpec } from '../ai/assistants.js';
import { monsterVoice } from '../audio/monsterVoice.js';
import * as F from '../ai/frankenstein.js';
import { usd } from '../core/money.js';

export class MonsterController {
  constructor({ den, ui, wf }) {
    this.den = den;
    this.ui = ui;
    this.wf = wf;
    this.gens = new Map(); // assistant id → { msgId, ctrl }
    this.waiting = new Map(); // assistant id → { msg, reply } while a run asks a question
    this.decisions = new Map(); // message id → decide(approve) for paid-step confirmations
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
  // While a run waits for the user's answer, the composer sends that answer.
  get generating() { return !!this.def && this.gens.has(this.def.id) && !this.waiting.has(this.def.id); }
  get awaitingAnswer() { return !!this.def && this.waiting.has(this.def.id); }

  select(id) {
    const changed = state.selectedMonster !== id;
    if (changed) this.onSelect?.(id);
    state.selectedMonster = id;
    save();
    this.den.setMonster(this.def);
    this.wf?.show(this.def);
    if (changed) this._sounds(this.def).then((s) => monsterVoice.sound(s?.arrive));
  }

  async _sounds(def) {
    try { return (await frankensteinSpec(def))?.voice?.sounds || null; } catch { return null; }
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
    const w = this.waiting.get(def.id);
    if (w) {
      this.waiting.delete(def.id);
      w.msg.question.answer = text;
      w.reply(text).catch((err) => { w.msg.question.error = err.message; this.ui.updateMessage(w.msg); });
      save();
      this.ui.updateMessage(w.msg);
      this.ui.renderControls();
      return true;
    }
    def.chat ||= [];
    def.chat.push({ id: uid('u'), role: 'user', content: text });
    const msg = { id: uid('a'), role: 'assistant', content: '', status: 'pending', steps: [] };
    def.chat.push(msg);
    save();
    this.ui.renderAll();
    this._run(def, msg, text);
    return true;
  }

  // Runs started from a live voice call go through the normal chat path, so
  // they show in the thread and the den; resolves with the backend run id.
  startVoiceRun(inputs) {
    return new Promise((resolve, reject) => {
      const def = this.def;
      if (!def || this.generating) return reject(new Error('This monster is busy with another answer.'));
      def.chat ||= [];
      def.chat.push({ id: uid('u'), role: 'user', content: Object.values(inputs).join(' · ') });
      const msg = { id: uid('a'), role: 'assistant', content: '', status: 'pending', steps: [] };
      def.chat.push(msg);
      save();
      this.ui.renderAll();
      this._run(def, msg, JSON.stringify(inputs), { onRun: resolve, voiced: true })
        .then(() => reject(new Error('The run did not start.')));
    });
  }

  voiceAnswer(text) {
    return this.awaitingAnswer ? this.send(text) : false;
  }

  voiceConfirm(approve) {
    const msg = [...(this.def?.chat || [])].reverse().find((m) => m.confirm?.state === 'pending' && this.decisions.has(m.id));
    if (!msg) return false;
    this.decide(msg.id, approve);
    return true;
  }

  // A finished live voice call, with its length and what ElevenLabs bills for it.
  async noteCall(defId, seconds) {
    const def = getMonster(defId);
    if (!def || seconds < 1) return;
    let rate = null;
    try { rate = (await F.pricing()).voice_call_per_min; } catch { /* offline: no price */ }
    const len = `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`;
    const cost = rate ? ` · ≈${usd((seconds / 60) * rate)} (ElevenLabs, ${usd(rate)}/min)` : '';
    def.chat ||= [];
    def.chat.push({ id: uid('a'), role: 'assistant', content: `_🎙 Voice call · ${len}${cost}_`, status: 'complete' });
    save();
    if (state.mode === 'monsters' && this.def?.id === defId) this.ui.renderAll();
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

  async _run(def, msg, input, { onRun, voiced = false } = {}) {
    const ctrl = new AbortController();
    this.gens.set(def.id, { msgId: msg.id, ctrl });
    msg.status = 'streaming';
    this.ui.updateMessage(msg);
    this.ui.renderControls();
    const live = () => state.mode === 'monsters' && this.den.def?.id === def.id;
    const history = def.chat.slice(0, -1).map(({ role, content }) => ({ role, content }));
    try {
      this.wf?.begin(def);
      const sounds = await this._sounds(def);
      for await (const ev of respond(def, input, { signal: ctrl.signal, history, onRun })) {
        if (ev.type === 'step') {
          if (live()) monsterVoice.startLoop(sounds?.working);
          msg.steps.push({ id: ev.step.id, label: ev.step.label, kind: ev.step.kind, limb: ev.step.limb, state: 'running' });
          this.wf?.step(def, ev.step);
          if (live()) this.den.onStep(ev.step);
        } else if (ev.type === 'stepDone') {
          const s = msg.steps.find((x) => x.id === ev.step.id);
          if (s) s.state = ev.outcome && ev.outcome !== 'done' ? ev.outcome : 'done';
          this.wf?.stepDone(def, ev.step, ev.outcome || 'done', ev.usage);
          if (live()) this.den.onStepDone(ev.step);
        } else if (ev.type === 'question') {
          msg.question = { text: ev.text, answer: null };
          this.waiting.set(def.id, { msg, reply: ev.reply });
          if (live()) this.den.onQuestion(ev.text);
          this.ui.renderControls();
        } else if (ev.type === 'confirm') {
          msg.confirm = { text: ev.text, credits: ev.credits, state: 'pending' };
          this.decisions.set(msg.id, ev.decide);
        } else if (ev.type === 'speech') {
          msg.speech = ev.text;
          msg.audio = ev.audio || null;
          this.wf?.speech(def, ev.usage);
          monsterVoice.stopLoop();
          if (live()) monsterVoice.sound(sounds?.done);
          // In a live voice call the agent reads the verdict itself.
          if (!voiced) setTimeout(() => monsterVoice.speak(ev.audio), sounds?.done ? 1200 : 0);
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
      this.wf?.finish(def, msg.status);
      monsterVoice.stopLoop();
      if (this.gens.get(def.id)?.msgId === msg.id) this.gens.delete(def.id);
      if (this.waiting.get(def.id)?.msg === msg) this.waiting.delete(def.id);
      this.decisions.delete(msg.id);
      if (msg.confirm?.state === 'pending') msg.confirm.state = 'expired';
      save();
      this.ui.updateMessage(msg);
      this.ui.renderControls();
    }
  }

  // Approve or decline a paid (credit-spending) step. Only ever from a click.
  decide(msgId, approve) {
    const fn = this.decisions.get(msgId);
    const msg = this.def?.chat.find((m) => m.id === msgId);
    if (!fn || !msg?.confirm) return;
    this.decisions.delete(msgId);
    msg.confirm.state = approve ? 'approved' : 'declined';
    fn(approve).catch((err) => { msg.confirm.error = err.message; this.ui.updateMessage(msg); });
    save();
    this.ui.updateMessage(msg);
  }

  // Stops following the run here. (Frankenstein has no cancel endpoint, so a
  // backend run keeps going server-side and its result is simply not shown.)
  stop() {
    if (!this.def) return;
    this.waiting.delete(this.def.id);
    this.gens.get(this.def.id)?.ctrl.abort();
  }
}
