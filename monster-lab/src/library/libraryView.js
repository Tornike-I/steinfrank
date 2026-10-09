// The Library tab: all your monsters, either roaming the 3D hall (click one to
// open it) or as cards with their job, body, voice, tools and cost per run.
import { state, save } from '../core/store.js';
import { allMonsters, titleOf } from '../monsters/registry.js';
import { themeOf } from '../monsters/themes.js';
import { bodyOf } from '../monsters/monsterGen.js';
import { limbKind } from '../monsters/limbParts.js';
import { TOOL } from '../chat/workflowView.js';
import * as F from '../ai/frankenstein.js';
import { usd, usageDetail } from '../core/money.js';

const PLAN = {
  classic: '🧟 Classic', hydra: '🐉 Hydra', flyer: '🦇 Flyer', jelly: '🪼 Floater',
  spider: '🕷 Spider', serpent: '🐍 Serpent', tower: '🗼 Tower',
};
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export class LibraryView {
  constructor(el, { menagerie, open, thumbnail }) {
    this.el = el;
    this.menagerie = menagerie;
    this.open = open;
    this.thumbnail = thumbnail;
    this.cards = new Map(); // Frankenstein monster id → API card
    state.libraryView ||= 'roam';
    el.innerHTML = `
      <div class="lib-bar">
        <div class="lib-title">Library <small></small></div>
        <div class="seg" role="tablist">
          <button type="button" data-view="roam" role="tab">Roam</button>
          <button type="button" data-view="cards" role="tab">Cards</button>
        </div>
      </div>
      <div class="lib-cards"></div>
      <div class="lib-hint">Click a monster to open it</div>`;
    el.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => {
      state.libraryView = b.dataset.view;
      save();
      this.render();
    }));
    el.querySelector('.lib-cards').addEventListener('click', (ev) => {
      const card = ev.target.closest('[data-id]');
      if (card) this.open(card.dataset.id);
    });
  }

  async show() {
    this.menagerie.sync(allMonsters());
    this.render();
    try {
      for (const c of await F.listMonsters()) this.cards.set(c.id, c);
      this.render();
    } catch { /* offline: cards show what the browser knows */ }
  }

  render() {
    const mons = allMonsters();
    const view = state.libraryView;
    this.el.dataset.view = view;
    this.el.querySelector('.lib-title small').textContent = `${mons.length} monster${mons.length === 1 ? '' : 's'}`;
    this.el.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
    const hint = this.el.querySelector('.lib-hint');
    hint.textContent = mons.length ? 'Click a monster to open it' : 'No monsters yet — build one in the Laboratory.';
    if (view !== 'cards') return;
    this.el.querySelector('.lib-cards').innerHTML = mons.map((d) => this._card(d)).join('')
      || '<div class="lib-empty">No monsters yet — build one in the Laboratory.</div>';
  }

  _card(d) {
    const body = bodyOf(d.seed, (d.form || 1) >= 2);
    const card = this.cards.get(d.provider?.monsterId);
    const chips = [`${PLAN[body.plan] || body.plan}${body.size !== 'normal' ? ` · ${body.size}` : ''}`];
    if (card?.voice?.archetype) chips.push(`🗣 ${card.voice.archetype}${card.voice.language === 'cs' ? ' · čeština' : ''}`);
    const est = (card?.workflow_estimate || card?.estimate)?.max;
    if (est) chips.push(est.usd ? `≤${usd(est.usd)} / run` : 'free to run');
    if (d.provider?.kind !== 'frankenstein') chips.push('simulated');
    const tools = [...new Set((d.limbs || []).map((l) => limbKind(l)))].map((k) => `<span title="${esc((TOOL[k] || TOOL.other).tool)}">${(TOOL[k] || TOOL.other).icon}</span>`).join('');
    return `<button type="button" class="lib-card" data-id="${esc(d.id)}">
      <img src="${esc(this.thumbnail(d) || '')}" alt="">
      <div class="lib-info">
        <h3>${esc(titleOf(d))}</h3>
        <div class="lib-sub">${esc(d.name)} · ${esc(themeOf(d.theme).label)}</div>
        <p>${esc(d.job || card?.purpose || d.provider?.purpose || '')}</p>
        <div class="lib-chips" title="${esc(est ? `Worst case per run, without the spoken verdict: ${usageDetail(est)}` : '')}">${chips.map((c) => `<span>${esc(c)}</span>`).join('')}</div>
        ${tools ? `<div class="lib-tools">${tools}</div>` : ''}
      </div>
    </button>`;
  }
}
