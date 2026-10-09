// Live voice call with a Frankenstein monster (ElevenLabs agent). The agent's
// client tools route through the MonsterController, so a spoken request runs
// exactly like a typed one: chat thread, den performance, sounds.
import { Conversation } from '@elevenlabs/client';
import * as F from './frankenstein.js';
import { monsterVoice } from '../audio/monsterVoice.js';

const ANNOUNCE = {
  completed: 'The task just finished. Call check_run now and tell me the result.',
  failed: 'The task failed. Call check_run and tell me briefly what happened.',
  needs_input: 'The task needs something from me. Call check_run and ask me.',
  needs_confirmation: 'The task wants to spend credits. Call check_run and ask me whether to go ahead.',
};

export class LiveVoice {
  constructor({ mon, onChange, onCallEnd }) {
    this.mon = mon;
    this.onChange = onChange;
    this.onCallEnd = onCallEnd;
    this.startedAt = 0;
    this.conv = null;
    this.monsterId = null;
    this.state = 'off'; // off | connecting | listening | speaking
    this._unfollow = [];
  }

  isOn(def) { return this.state !== 'off' && this.monsterId === def?.id; }

  _set(state) { this.state = state; this.onChange?.(state); }

  async toggle(def) {
    if (this.state !== 'off') return this.stop();
    if (def?.provider?.kind !== 'frankenstein') throw new Error('Only monsters built by Frankenstein can talk.');
    this.monsterId = def.id;
    this._set('connecting');
    try {
      const { signed_url: signedUrl } = await F.voiceSession(def.provider.monsterId);
      monsterVoice.stopAll();
      this.conv = await Conversation.startSession({
        signedUrl,
        clientTools: this._tools(),
        onConnect: () => { this.startedAt = Date.now(); this._set('listening'); },
        onDisconnect: () => this._ended(),
        onModeChange: ({ mode }) => { if (this.conv) this._set(mode === 'speaking' ? 'speaking' : 'listening'); },
        onError: (e) => console.warn('[voice]', e),
      });
      monsterVoice.live = () => (this.conv ? Math.min(1, this.conv.getOutputVolume() * 2.5) : 0);
    } catch (err) {
      this._ended();
      throw err;
    }
  }

  async stop() {
    const conv = this.conv;
    this.conv = null;
    await conv?.endSession().catch(() => {});
    this._ended();
  }

  _ended() {
    if (this.startedAt) this.onCallEnd?.(this.monsterId, (Date.now() - this.startedAt) / 1000);
    this.startedAt = 0;
    this.conv = null;
    monsterVoice.live = null;
    for (const off of this._unfollow) off();
    this._unfollow = [];
    this._set('off');
  }

  // Speaks up on its own when the run changes state, instead of waiting to be asked.
  _follow(runId) {
    let last = null;
    const off = F.watchRun(runId, (run) => {
      if (run.status === last) return;
      last = run.status;
      const say = ANNOUNCE[run.status] || (run.status === 'blocked' && ANNOUNCE.failed);
      if (say && this.conv) this.conv.sendUserMessage(`(${say} run_id ${runId})`);
    });
    this._unfollow.push(off);
  }

  _tools() {
    return {
      start_run: async (inputs) => {
        try {
          const runId = await this.mon.startVoiceRun(inputs);
          this._follow(runId);
          return JSON.stringify({ run_id: runId, status: 'queued' });
        } catch (err) {
          return JSON.stringify({ status: 'failed', error: err.message });
        }
      },
      check_run: async ({ run_id }) => {
        const run = await F.getRun(run_id);
        return JSON.stringify({
          status: run.status, speech: run.output?.speech, error: run.error,
          question: run.needs_input?.message ?? run.confirm?.message,
        });
      },
      answer_input: async ({ run_id, answer }) => {
        if (!this.mon.voiceAnswer(answer)) await F.answer(run_id, answer);
        return JSON.stringify({ status: 'sent' });
      },
      confirm_spend: async ({ run_id, approve }) => {
        if (!this.mon.voiceConfirm(approve)) await F.confirm(run_id, approve);
        return JSON.stringify({ status: approve ? 'approved' : 'declined' });
      },
    };
  }
}
