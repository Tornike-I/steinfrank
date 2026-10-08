// DOM for the app. The sidebar is the tab list: a permanent Laboratory tab
// plus one persistent tab per topic monster. The Lab shows only the scene and
// a prompt; each monster tab shows its den above a familiar chat.
import { state, save } from '../core/store.js';
import { renderMarkdown } from './markdown.js';
import { allMonsters } from '../monsters/registry.js';
import { themeOf } from '../monsters/themes.js';

const GREETINGS = ['Greetings, you absolute specimen.', 'Greetings, you absolute specimen.', 'Ask me anything. I have spare parts.', 'Lie down. Tell me everything.'];

const LAB_IDEAS = [
  'Will it rain in Prague tomorrow?',
  'How do I make a proper carbonara?',
  'Plan three days in Lisbon',
  'What is 17% of 2,450?',
  'Prep me for my call with Acme Corp',
  'Why do cats knead blankets?',
];

const ICON = {
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
      app: $('#app'), main: $('#main'), list: $('#conv-list'), labTab: $('#lab-tab'), thread: $('#thread'), messages: $('#messages'),
      form: $('#composer'), input: $('#prompt'), send: $('#send'), greeting: $('#greeting'), ideas: $('#ideas'), banner: $('#topic-banner'),
      toggleSide: $('#toggle-sidebar'), toggleSide2: $('#toggle-sidebar-2'),
      sound: $('#sound'), settings: $('#settings'), pop: $('#settings-pop'), threshold: $('#threshold'),
      thresholdVal: $('#threshold-val'), count: $('#specimen-count'), model: $('#model'), disclaimer: $('#disclaimer'),
    };
    this.nodes = new Map();
    this._raf = new Map();
    this.stickToBottom = true;
  }

  get ctrl() { return state.mode === 'monsters' ? this.mon : this.lab; }

  bind({ lab, mon, onSound, onThreshold, getCount, onMode, thumbnail }) {
    Object.assign(this, { lab, mon, getCount, onMode, thumbnail });
    const e = this.el;
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
    if (mode === 'monsters') this.mon.den.setMonster(this.mon.def);
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
    const conv = lab ? null : this.mon.conv;
    e.main.classList.toggle('is-empty', !lab && (!conv || conv.messages.length === 0));
    if (lab) {
      e.greeting.textContent = this._greeting;
      e.input.placeholder = 'Ask anything — the doctor will build (or fetch) the right monster…';
      e.disclaimer.textContent = 'Each topic gets its own monster. Dr. Stitchwick can make mistakes. Mostly on purpose.';
    } else {
      const th = def ? themeOf(def.theme) : null;
      e.greeting.textContent = def ? `${def.name} ${th.intro}` : '';
      e.input.placeholder = th?.placeholder || '';
      e.disclaimer.textContent = def ? `${def.name} · ${th.label} assistant · simulated responses` : '';
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
    const list = e.list;
    list.innerHTML = '';
    const mons = allMonsters();
    if (!mons.length) {
      list.innerHTML = '<div class="conv-empty">No monsters yet. Ask the doctor a question in the Laboratory.</div>';
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
      text.textContent = th.label;
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
    const conv = state.mode === 'lab' ? null : this.mon.conv;
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
    if (!m.content && running) body.innerHTML = '';
    else body.innerHTML = renderMarkdown(m.content || '') + (m.status === 'streaming' && m.content ? '<span class="caret"></span>' : '');
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
      const conv = state.mode === 'lab' ? null : this.mon.conv;
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
    if (state.mode === 'lab') return;
    const conv = this.mon?.conv;
    if (!conv) return;
    const last = conv.messages[conv.messages.length - 1];
    for (const [id, row] of this.nodes) {
      const r = row.querySelector('.regen');
      if (r) r.hidden = !(last && last.id === id) || this.mon.generating;
    }
  }

  renderControls() {
    this.el.app.classList.toggle('lab-busy', !!this.lab?.generating);
    const c = this.ctrl;
    const gen = c?.generating;
    const b = this.el.send;
    b.innerHTML = gen ? ICON.stop : ICON.send;
    b.classList.toggle('is-stop', !!gen);
    b.title = gen ? 'Stop' : 'Send';
    b.setAttribute('aria-label', b.title);
    b.disabled = !gen && !this.el.input.value.trim();
    this._refreshActions();
    this.renderSidebarBusy();
  }

  renderHeader() {
    const e = this.el;
    const on = state.settings.sound;
    e.sound.classList.toggle('on', on);
    e.sound.title = on ? 'Mute sound' : 'Enable sound';
    e.sound.setAttribute('aria-pressed', String(on));
    e.thresholdVal.textContent = state.settings.threshold;
    e.count.textContent = `${this.getCount ? this.getCount() : 0} / ${state.settings.threshold}`;
    if (state.mode === 'lab') e.model.innerHTML = 'Stitchwick <em>Laboratory</em>';
    else {
      const d = this.mon.def;
      e.model.innerHTML = d ? `${esc(themeOf(d.theme).label)} <em>${esc(d.name)}</em>` : '';
    }
  }
}
