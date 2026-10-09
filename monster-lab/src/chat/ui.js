// DOM for the app. The sidebar is the tab list: a permanent Laboratory tab
// plus one persistent tab per topic monster. The Lab shows only the scene and
// a prompt; each monster tab shows its den above a familiar chat.
import { state, save } from '../core/store.js';
import { renderMarkdown } from './markdown.js';
import { allMonsters, titleOf } from '../monsters/registry.js';
import { themeOf } from '../monsters/themes.js';
import { backend } from '../ai/frankenstein.js';
import { monsterVoice } from '../audio/monsterVoice.js';

const GREETINGS = ['What shall we build tonight?', 'Name a chore. I have spare parts.', 'Every tedious job deserves a monster.', 'Describe the job. I will stitch the beast.'];

// Jobs, not questions: the lab only builds monsters.
const LAB_IDEAS = [
  'Check whether an online shop is a scam',
  'Prep a briefing on a company before a meeting',
  'Audit a landing page and list the top fixes',
  'Tell me if an Amazon product is worth buying',
  'Summarize any web page in three sentences',
  'Give me the weather outlook for a city',
];

const ICON = {
  mic: '<svg viewBox="0 0 24 24" width="18" height="18"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  send: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  stop: '<svg viewBox="0 0 24 24" width="14" height="14"><rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor"/></svg>',
  copy: '<svg viewBox="0 0 24 24" width="16" height="16"><rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
  regen: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M18 3v4h-4M6 21v-4h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M5 12l5 5 9-10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export class ChatUI {
  constructor() {
    this.el = {
      app: $('#app'), main: $('#main'), list: $('#conv-list'), labTab: $('#lab-tab'), libraryTab: $('#library-tab'), thread: $('#thread'), messages: $('#messages'),
      form: $('#composer'), input: $('#prompt'), send: $('#send'), mic: $('#mic'), greeting: $('#greeting'), ideas: $('#ideas'), banner: $('#topic-banner'),
      toggleSide: $('#toggle-sidebar'), toggleSide2: $('#toggle-sidebar-2'),
      sound: $('#sound'), settings: $('#settings'), pop: $('#settings-pop'), threshold: $('#threshold'),
      thresholdVal: $('#threshold-val'), count: $('#specimen-count'), model: $('#model'), disclaimer: $('#disclaimer'), backend: $('#backend-chip'),
    };
    this.nodes = new Map();
    this._raf = new Map();
    this.stickToBottom = true;
  }

  get ctrl() { return state.mode === 'monsters' ? this.mon : this.lab; }

  bind({ lab, mon, voice, library, onSound, onThreshold, getCount, onMode, thumbnail }) {
    Object.assign(this, { lab, mon, voice, library, getCount, onMode, thumbnail });
    const e = this.el;
    e.mic.innerHTML = ICON.mic;
    e.mic.addEventListener('click', async () => {
      try { await this.voice.toggle(this.mon.def); } catch (err) { this.banner(`Couldn't start the voice call: ${esc(err.message)}`, { timeout: 7000 }); }
    });
    e.form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const c = this.ctrl;
      if (c.generating) { c.stop(); return; }
      if (c.send(e.input.value)) { e.input.value = ''; this._autosize(); this.renderControls(); }
    });
    e.input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        if (!this.ctrl.generating) e.form.requestSubmit();
      }
    });
    e.input.addEventListener('input', () => { this._autosize(); this.renderControls(); });
    e.labTab.addEventListener('click', () => this.setMode('lab'));
    e.libraryTab.addEventListener('click', () => this.setMode('library'));
    e.libraryTab.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') this.setMode('library'); });
    const toggle = () => {
      e.app.classList.toggle('sidebar-collapsed');
      try { localStorage.setItem('stitchwick-lab.sidebar', e.app.classList.contains('sidebar-collapsed') ? '0' : '1'); } catch { /* ignore */ }
    };
    e.toggleSide.addEventListener('click', toggle);
    e.toggleSide2.addEventListener('click', toggle);
    try { if (localStorage.getItem('stitchwick-lab.sidebar') === '0') e.app.classList.add('sidebar-collapsed'); } catch { /* ignore */ }

    e.sound.addEventListener('click', () => {
      state.settings.sound = !state.settings.sound;
      onSound(state.settings.sound);
      this.renderHeader();
    });
    e.settings.addEventListener('click', (ev) => {
      ev.stopPropagation();
      e.pop.hidden = !e.pop.hidden;
      e.settings.setAttribute('aria-expanded', String(!e.pop.hidden));
    });
    document.addEventListener('click', (ev) => {
      if (!e.pop.hidden && !e.pop.contains(ev.target)) { e.pop.hidden = true; e.settings.setAttribute('aria-expanded', 'false'); }
    });
    e.threshold.value = state.settings.threshold;
    e.threshold.addEventListener('input', () => {
      state.settings.threshold = Number(e.threshold.value);
      save();
      this.renderHeader();
      onThreshold();
    });
    e.thread.addEventListener('scroll', () => {
      const t = e.thread;
      this.stickToBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 80;
    });
    this._greeting = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
    setInterval(() => this.renderHeader(), 500);
  }

  // Switch between the Laboratory and a monster's tab. Nothing is cleared.
  setMode(mode, monsterId) {
    const changed = monsterId && monsterId !== state.selectedMonster;
    if (changed) this.mon.select(monsterId);
    if (state.mode === mode && !changed) return;
    state.mode = mode;
    save();
    if (mode === 'monsters') { this.mon.den.setMonster(this.mon.def); this.mon.wf?.show(this.mon.def); }
    if (mode === 'library') this.library?.show();
    this.onMode?.(mode);
    this.renderAll();
  }

  openMonster(id) { this.setMode('monsters', id); }

  focusInput() { this.el.input.focus(); }

  // Topic banner over the lab ("Creating a Weather monster").
  banner(html, { busy = false, timeout = 0 } = {}) {
    const b = this.el.banner;
    clearTimeout(this._bannerT);
    if (!html) { b.hidden = true; return; }
    b.innerHTML = (busy ? '<span class="spin on"></span>' : '') + `<span>${html}</span>`;
    b.hidden = false;
    if (timeout) this._bannerT = setTimeout(() => { b.hidden = true; }, timeout);
  }

  _autosize() {
    const i = this.el.input;
    i.style.height = 'auto';
    i.style.height = Math.min(i.scrollHeight, 200) + 'px';
  }

  renderAll() {
    const lab = state.mode === 'lab';
    const e = this.el;
    e.app.dataset.mode = state.mode;
    const def = this.mon.def;
    const conv = state.mode === 'monsters' ? this.mon.conv : null;
    e.main.classList.toggle('is-empty', state.mode === 'monsters' && (!conv || conv.messages.length === 0));
    if (state.mode === 'library') {
      e.greeting.textContent = '';
    } else if (lab) {
      e.greeting.textContent = this._greeting;
      e.input.placeholder = 'Describe a job for a new monster…';
      e.disclaimer.textContent = 'Every job gets its own monster. Dr. Frankenstein can make mistakes. Mostly on purpose.';
    } else {
      const th = def ? themeOf(def.theme) : null;
      e.greeting.textContent = def ? `${def.name} ${th.intro}` : '';
      e.input.placeholder = this._placeholder(def);
      const real = def?.provider?.kind === 'frankenstein';
      e.disclaimer.textContent = def ? `${def.name} · ${real ? 'built by Frankenstein' : 'simulated responses'}` : '';
    }
    this.renderIdeas();
    this.renderSidebar();
    this.renderMessages();
    this.renderControls();
    this.renderHeader();
  }

  renderIdeas() {
    const box = this.el.ideas;
    box.innerHTML = '';
    if (state.mode !== 'lab') return;
    for (const text of LAB_IDEAS) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'idea';
      b.textContent = text;
      b.addEventListener('click', () => {
        this.el.input.value = text;
        this._autosize();
        this.renderControls();
        this.el.input.focus();
      });
      box.append(b);
    }
  }

  renderSidebar() {
    const e = this.el;
    e.labTab.classList.toggle('active', state.mode === 'lab');
    e.libraryTab.classList.toggle('active', state.mode === 'library');
    const list = e.list;
    list.innerHTML = '';
    const mons = allMonsters();
    if (!mons.length) {
      list.innerHTML = '<div class="conv-empty">No monsters yet. Describe a job in the Laboratory.</div>';
      return;
    }
    for (const d of mons) {
      const th = themeOf(d.theme);
      const item = document.createElement('div');
      item.className = 'conv monster-item' + (state.mode === 'monsters' && d.id === this.mon.def?.id ? ' active' : '');
      item.dataset.id = d.id;
      item.setAttribute('role', 'tab');
      item.tabIndex = 0;
      const img = document.createElement('img');
      img.className = 'thumb';
      img.alt = '';
      img.src = this.thumbnail?.(d) || '';
      const text = document.createElement('span');
      text.className = 'conv-title';
      text.textContent = titleOf(d);
      text.title = d.job || th.label;
      const sub = document.createElement('small');
      sub.textContent = d.name;
      text.append(sub);
      item.append(img, text);
      const open = () => this.openMonster(d.id);
      item.addEventListener('click', open);
      item.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') open(); });
      list.append(item);
    }
    this.renderSidebarBusy();
  }

  // "Answering" spinners in the tab list.
  renderSidebarBusy() {
    for (const it of this.el.list.querySelectorAll('.monster-item')) {
      const busy = this.mon?.gens.has(it.dataset.id);
      const sp = it.querySelector('.spin');
      if (busy && !sp) it.insertAdjacentHTML('beforeend', '<span class="spin on" title="Answering"></span>');
      if (!busy && sp) sp.remove();
    }
  }

  renderMessages() {
    const conv = state.mode === 'monsters' ? this.mon.conv : null;
    const box = this.el.messages;
    box.innerHTML = '';
    this.nodes.clear();
    if (!conv) return;
    for (const m of conv.messages) box.append(this._node(m));
    this._refreshActions();
    this.stickToBottom = true;
    requestAnimationFrame(() => { this.el.thread.scrollTop = this.el.thread.scrollHeight; });
  }

  _node(m) {
    const row = document.createElement('div');
    row.className = `msg ${m.role}`;
    row.dataset.id = m.id;
    if (m.steps) {
      const steps = document.createElement('div');
      steps.className = 'steps';
      row.append(steps);
    }
    const body = document.createElement('div');
    body.className = 'msg-body';
    row.append(body);
    if (m.role === 'assistant') {
      const foot = document.createElement('div');
      foot.className = 'msg-foot';
      row.append(foot);
    }
    this.nodes.set(m.id, row);
    this._fill(m, row);
    return row;
  }

  _fill(m, row) {
    const body = row.querySelector('.msg-body');
    if (m.role === 'user') {
      body.textContent = m.content;
      return;
    }
    const running = m.status === 'streaming' || m.status === 'pending';
    row.classList.toggle('streaming', running);
    if (m.steps) this._fillSteps(m, row.querySelector('.steps'));
    let html = '';
    if (m.question) {
      html += `<div class="ask"><b>${esc(this.mon.def?.name || 'The monster')} asks:</b> ${esc(m.question.text)}`
        + (m.question.answer ? `<div class="ask-answer">You: ${esc(m.question.answer)}</div>` : '<div class="ask-hint">Type your answer below.</div>')
        + (m.question.error ? `<div class="note error">${esc(m.question.error)}</div>` : '') + '</div>';
    }
    if (m.confirm) {
      const c = m.confirm;
      html += `<div class="confirm-card" data-state="${c.state}"><div>${esc(c.text)}</div>`
        + (c.state === 'pending'
          ? `<div class="confirm-actions"><button type="button" class="meet" data-approve="1">Approve ${esc(c.credits)} credits</button><button type="button" class="ghost" data-approve="0">Decline</button></div>`
          : `<div class="note">${c.state === 'approved' ? 'Approved.' : c.state === 'declined' ? 'Declined — it will do without.' : 'No longer waiting.'}</div>`)
        + (c.error ? `<div class="note error">${esc(c.error)}</div>` : '') + '</div>';
    }
    if (m.speech) html += `<div class="speech">“${esc(m.speech)}”${m.audio ? '<button type="button" class="hear" title="Play the spoken answer">▶ Hear it</button>' : ''}</div>`;
    if (m.content || !running) html += renderMarkdown(m.content || '') + (m.status === 'streaming' && m.content ? '<span class="caret"></span>' : '');
    body.innerHTML = html;
    body.querySelectorAll('[data-approve]').forEach((b) => b.addEventListener('click', () => this.mon.decide(m.id, b.dataset.approve === '1')));
    body.querySelector('.hear')?.addEventListener('click', () => monsterVoice.speak(m.audio, true));
    const foot = row.querySelector('.msg-foot');
    foot.innerHTML = '';
    if (m.status === 'stopped') foot.insertAdjacentHTML('beforeend', '<span class="note">Stopped.</span>');
    if (m.status === 'error') {
      const n = document.createElement('span');
      n.className = 'note error';
      n.textContent = m.error || 'Something went wrong.';
      foot.append(n);
    }
    if (m.status === 'complete' || m.status === 'stopped' || m.status === 'error') {
      const copy = this._btn(ICON.copy, 'Copy', () => {
        navigator.clipboard?.writeText(m.content).then(() => { copy.innerHTML = ICON.check; setTimeout(() => (copy.innerHTML = ICON.copy), 1200); });
      });
      if (m.content) foot.append(copy);
      const regen = this._btn(ICON.regen, m.status === 'error' ? 'Retry' : 'Regenerate', () => this.mon.regenerate(m.id));
      regen.classList.add('regen');
      if (m.status === 'error') regen.insertAdjacentHTML('beforeend', '<span>Retry</span>');
      foot.append(regen);
    }
  }

  // The live workflow checklist ("thinking" box) above a monster's answer.
  _fillSteps(m, box) {
    const running = m.steps.find((s) => s.state === 'running');
    const done = m.steps.filter((s) => s.state === 'done').length;
    const prev = box.querySelector('details');
    const open = prev ? prev.open : true;
    const head = running ? `${esc(running.label)}…`
      : m.status === 'complete' || m.status === 'streaming' ? (m.steps.length ? `Worked through ${done} step${done === 1 ? '' : 's'}` : 'Waking up…')
        : m.status === 'pending' ? 'Waking up…' : `Stopped after ${done} step${done === 1 ? '' : 's'}`;
    const spinning = !!running || m.status === 'pending' || (m.status === 'streaming' && !m.steps.length);
    box.innerHTML = `<details ${open || running ? 'open' : ''}><summary><span class="spin ${spinning ? 'on' : ''}"></span>${head}</summary><ol>${m.steps.map((s) => `<li class="${s.state}"><span class="tick"></span>${esc(s.label)}</li>`).join('')}</ol></details>`;
  }

  _btn(icon, label, fn) {
    const b = document.createElement('button');
    b.className = 'icon-btn';
    b.type = 'button';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = icon;
    b.addEventListener('click', fn);
    return b;
  }

  // Streaming updates are coalesced to one DOM write per frame.
  updateMessage(m) {
    if (this._raf.has(m.id)) return;
    this._raf.set(m.id, requestAnimationFrame(() => {
      this._raf.delete(m.id);
      const conv = state.mode === 'monsters' ? this.mon.conv : null;
      if (!conv || !conv.messages.includes(m)) return;
      let row = this.nodes.get(m.id);
      if (!row) { row = this._node(m); this.el.messages.append(row); }
      else this._fill(m, row);
      this._refreshActions();
      if (this.stickToBottom) this.el.thread.scrollTop = this.el.thread.scrollHeight;
    }));
  }

  // Regenerate is only offered on the latest answer, when idle.
  _refreshActions() {
    if (state.mode !== 'monsters') return;
    const conv = this.mon?.conv;
    if (!conv) return;
    const last = conv.messages[conv.messages.length - 1];
    for (const [id, row] of this.nodes) {
      const r = row.querySelector('.regen');
      if (r) r.hidden = !(last && last.id === id) || this.mon.generating;
    }
  }

  renderControls() {
    this.el.app.classList.toggle('lab-busy', !!this.lab?.generating || !!this.lab?.director.cleaning);
    const c = this.ctrl;
    const gen = c?.generating;
    const b = this.el.send;
    b.innerHTML = gen ? ICON.stop : ICON.send;
    b.classList.toggle('is-stop', !!gen);
    b.title = gen ? 'Stop' : 'Send';
    b.setAttribute('aria-label', b.title);
    b.disabled = !gen && !this.el.input.value.trim();
    if (state.mode === 'monsters' && this.mon?.awaitingAnswer) this.el.input.placeholder = `Answer ${this.mon.def.name}'s question…`;
    else if (state.mode === 'monsters' && this.mon?.def) this.el.input.placeholder = this._placeholder(this.mon.def);
    this._renderMic();
    this._refreshActions();
    this.renderSidebarBusy();
  }

  // Forged monsters say which inputs they need; several go as "name: value" lines.
  _placeholder(def) {
    const props = Object.entries(def?.provider?.inputs?.properties || {});
    if (!props.length) return themeOf(def?.theme).placeholder;
    if (props.length === 1) return props[0][1].description || `Give me the ${props[0][0]}…`;
    return `Give me ${props.map(([k]) => `${k}: …`).join(' · ')}`;
  }

  _renderMic() {
    const def = state.mode === 'monsters' ? this.mon?.def : null;
    const mic = this.el.mic;
    const canTalk = !!def && def.provider?.kind === 'frankenstein';
    mic.hidden = !canTalk && !this.voice?.isOn(def);
    mic.dataset.state = this.voice?.isOn(def) ? this.voice.state : 'off';
    mic.title = mic.dataset.state === 'off' ? 'Talk to this monster' : 'Hang up';
    mic.setAttribute('aria-label', mic.title);
  }

  renderHeader() {
    const e = this.el;
    e.backend.dataset.on = backend.connected ? '1' : '0';
    e.backend.textContent = !backend.checked ? 'Frankenstein…' : backend.connected ? 'Frankenstein' : 'Offline · scripted';
    e.backend.title = backend.connected ? 'Connected to the Frankenstein API: new monsters are forged for real' : 'Frankenstein API not reachable: new monsters use scripted workflows';
    const on = state.settings.sound;
    e.sound.classList.toggle('on', on);
    e.sound.title = on ? 'Mute sound' : 'Enable sound';
    e.sound.setAttribute('aria-pressed', String(on));
    e.thresholdVal.textContent = state.settings.threshold;
    e.count.textContent = `${this.getCount ? this.getCount() : 0} / ${state.settings.threshold}`;
    if (state.mode === 'lab') e.model.innerHTML = 'Frankenstein <em>Laboratory</em>';
    else if (state.mode === 'library') e.model.innerHTML = 'Frankenstein <em>Library</em>';
    else {
      const d = this.mon.def;
      e.model.innerHTML = d ? `${esc(titleOf(d))} <em>${esc(d.name)}</em>` : '';
    }
  }
}
