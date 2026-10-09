// A monster's workflow as boxes, one per step, joined top to bottom (parallel
// steps side by side). Boxes light up while their step runs and then show
// what the step actually cost; before a run they show Frankenstein's estimate.
import { limbKind, limbInfo } from '../monsters/limbParts.js';
import { frankensteinSpec } from '../ai/assistants.js';
import { usd, usageDetail, addUsage } from '../core/money.js';

export const TOOL = {
  web_search: { icon: '🔭', tool: 'Web search · Tavily' },
  http_fetch: { icon: '🦾', tool: 'Page fetch · HTTP' },
  llm: { icon: '🧠', tool: 'LLM · OpenAI' },
  forged: { icon: '🪚', tool: 'Forged Python' },
  sokosumi: { icon: '🐙', tool: 'Hired agent · Sokosumi' },
  tts: { icon: '📯', tool: 'Voice · ElevenLabs' },
  sfx: { icon: '🪗', tool: 'Sound · ElevenLabs' },
  image: { icon: '📷', tool: 'Image · OpenAI' },
  notify: { icon: '🔔', tool: 'Notify' },
  other: { icon: '🦴', tool: 'Tool' },
};
const SCRIPTED_LIMB = { gather: 'web_search', read: 'http_fetch', think: 'llm', compute: 'forged', write: 'llm' };

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const human = (id) => String(id).replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
function costText(u, estimate) {
  if (!u) return '';
  if (!u.usd) return estimate ? 'free' : '$0';
  return `${estimate ? '≤' : ''}${usd(u.usd)}`;
}

const costHtml = (u, estimate) => (u ? `<div class="wf-cost" title="${esc(usageDetail(u))}">${esc(costText(u, estimate))}</div>` : '');

export class WorkflowView {
  constructor(el) {
    this.el = el;
    this.def = null;
    this.layout = new Map(); // monster id → { rows, estimates, speaks }
    this.live = new Map(); // monster id → { steps: Map(id → { state, usage }), order: [], speech }
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('.wf-toggle')) el.classList.toggle('collapsed');
    });
    // Hired agents take minutes; a ticking clock shows the step isn't frozen.
    setInterval(() => {
      for (const t of el.querySelectorAll('.wf-elapsed')) {
        const s = Math.max(0, Math.round((Date.now() - Number(t.dataset.since)) / 1000));
        t.textContent = `Hired agent working · ${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s (often 3–6 min)`;
      }
    }, 1000);
  }

  async show(def) {
    if (!this._sized) {
      this._sized = true;
      this.el.classList.toggle('collapsed', window.innerWidth < 1100);
    }
    this.def = def;
    this.render();
    if (!def || this.layout.has(def.id)) return;
    try {
      const spec = await frankensteinSpec(def);
      if (spec) {
        this.layout.set(def.id, {
          rows: spec.steps.map((g) => (g.parallel || [g]).map((l) => ({ id: l.id, limb: l.limb, label: human(l.id), note: l.note, when: l.when, each: l.for_each?.max }))),
          estimates: spec.step_estimates || {},
          speaks: spec.speak !== false,
        });
      }
    } catch { /* offline: boxes appear from the run itself */ }
    if (this.def?.id === def.id) this.render();
  }

  _run(def) {
    if (!this.live.has(def.id)) this.live.set(def.id, { steps: new Map(), order: [], speech: null, status: null });
    return this.live.get(def.id);
  }

  begin(def) {
    this.live.set(def.id, { steps: new Map(), order: [], speech: null, status: 'running' });
    this._maybeRender(def);
  }

  step(def, step) {
    const r = this._run(def);
    if (!r.steps.has(step.id)) r.order.push(step);
    r.steps.set(step.id, { state: 'running', usage: null, since: Date.now() });
    this._maybeRender(def);
  }

  stepDone(def, step, outcome = 'done', usage = null) {
    const r = this._run(def);
    if (!r.steps.has(step.id)) r.order.push(step);
    r.steps.set(step.id, { state: outcome === 'done' ? 'done' : outcome, usage });
    this._maybeRender(def);
  }

  speech(def, usage = null) {
    const r = this._run(def);
    r.speech = 'done';
    r.speechUsage = usage;
    this._maybeRender(def);
  }

  finish(def, status) {
    const r = this._run(def);
    r.status = status;
    for (const s of r.steps.values()) if (s.state === 'running') s.state = status === 'complete' ? 'done' : 'cancelled';
    this._maybeRender(def);
  }

  _maybeRender(def) { if (this.def?.id === def.id) this.render(); }

  // Scripted (offline) monsters have no spec: their boxes are the steps seen so far.
  _rows(def) {
    const lay = this.layout.get(def.id);
    if (lay) return lay;
    const r = this.live.get(def.id);
    return { rows: (r?.order || []).map((s) => [{ id: s.id, limb: s.limb || SCRIPTED_LIMB[s.kind] || 'other', label: s.label }]), estimates: {}, speaks: false };
  }

  render() {
    const def = this.def;
    this.el.hidden = !def;
    if (!def) return;
    const { rows, estimates, speaks } = this._rows(def);
    const run = this.live.get(def.id);
    let total = {};
    // The spoken verdict is a delivery cost, not the workflow's: it is shown on its own box only.
    for (const s of run?.steps.values() || []) total = addUsage(total, s.usage);
    const estTotal = Object.entries(estimates).filter(([k]) => k !== 'speech').reduce((a, [, u]) => addUsage(a, u), {});
    const head = run?.steps.size ? `this run: ${costText(total)}` : rows.length ? `per run: ${costText(estTotal, true)}` : '';

    const box = (b) => {
      const kind = limbKind(b.limb);
      const t = TOOL[kind] || TOOL.other;
      const live = run?.steps.get(b.id);
      const state = live?.state || 'idle';
      const cost = live?.usage ? costHtml(live.usage) : costHtml(estimates[b.id], true);
      const tool = kind === 'forged' ? `${t.tool} · ${esc(b.limb.slice(7).replace(/_/g, ' '))}` : t.tool;
      return `<div class="wf-box" data-state="${state}" title="${esc(b.note || b.label)} — ${esc(limbInfo(b.limb).label)}">
        <div class="wf-icon">${t.icon}</div>
        <div class="wf-text"><b>${esc(b.label)}</b>${b.note ? `<span class="wf-note">${esc(b.note)}</span>` : ''}<small>${esc(tool)}</small>
          ${state === 'running' && kind === 'sokosumi' ? `<em class="wf-tag wf-elapsed" data-since="${live.since}">Hired agent working…</em>` : ''}
          ${b.when ? `<em class="wf-tag">only if ${esc(b.when)}</em>` : ''}${b.each ? `<em class="wf-tag">× up to ${b.each}</em>` : ''}</div>
        ${cost}
      </div>`;
    };
    const speakRow = speaks
      ? `<div class="wf-row"><div class="wf-box" data-state="${run?.speech || 'idle'}"><div class="wf-icon">📯</div><div class="wf-text"><b>Speak the verdict</b><span class="wf-note">Rewritten for listening, then voiced · not in the total</span><small>LLM · OpenAI → Voice · ElevenLabs</small></div>${run?.speechUsage ? costHtml(run.speechUsage) : costHtml(estimates.speech, true)}</div></div>`
      : '';
    const body = rows.length
      ? rows.map((row) => `<div class="wf-row${row.length > 1 ? ' wf-par' : ''}">${row.length > 1 ? '<span class="wf-par-label">in parallel</span>' : ''}${row.map(box).join('')}</div>`).join('') + speakRow
      : '<div class="wf-empty">Its steps appear here as it works.</div>';
    this.el.innerHTML = `<div class="wf-head"><span>Workflow</span><small>${esc(head)}</small><button type="button" class="wf-toggle" title="Show or hide the workflow" aria-label="Toggle workflow">▾</button></div><div class="wf-flow">${body}</div>`;
  }
}
