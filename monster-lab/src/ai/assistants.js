// Provider layer for topic assistants. Every assistant carries its own
// `provider` config, so each topic monster can later point at a different
// model / endpoint / system prompt (`assistant.instructions`).
//
// A provider returns an async iterable of workflow events:
//   { type: 'step', step } | { type: 'stepDone', step } | { type: 'token', text }
// and must honour `signal` (throw AbortError) and throw on failure.
// Extra interactive events (Frankenstein provider):
//   { type: 'question', text, reply(text) }        the run needs input
//   { type: 'confirm', text, credits, decide(bool) } a paid step needs approval
//   { type: 'speech', text, audio }                  the short spoken verdict
import { runWorkflow } from '../workflows/engine.js';
import { scoreTopics, themeOf } from '../monsters/themes.js';
import { limbInfo } from '../monsters/limbParts.js';
import * as F from './frankenstein.js';
import { addUsage } from '../core/money.js';

const PROVIDERS = {
  // Simulated: themed steps + scripted answer.
  scripted: (assistant, input, opts) => runWorkflow(assistant, input, opts),
  // The real thing: a published Frankenstein monster run.
  frankenstein: frankensteinRun,
};

// Workflow step kinds (what the monster acts out) for each limb.
const KIND_FOR_VERB = { look: 'gather', read: 'read', think: 'think', compute: 'compute', write: 'write', speak: 'write' };
export const kindForLimb = (limb) => KIND_FOR_VERB[limbInfo(limb).verb] || 'think';

const human = (id) => id.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

function leavesOf(spec) {
  const out = [];
  spec.steps.forEach((s, top) => {
    for (const leaf of s.parallel || [s]) out.push({ id: leaf.id, limb: leaf.limb, note: leaf.note || '', top });
  });
  return out;
}

// Turn a free-text question into the monster's inputs.
export function mapInputs(schema, text) {
  const t = text.trim();
  if (t.startsWith('{')) { try { return JSON.parse(t); } catch { /* not JSON */ } }
  const props = schema?.properties || {};
  if (props.question) return { question: t };
  const out = {};
  // "name: value" pairs (lines, ";" or "·") fill several inputs at once.
  const names = Object.keys(props);
  if (names.length > 1) {
    for (const part of t.split(/\n|;|·/)) {
      const m = part.match(/^\s*([\w -]+?)\s*[:=]\s*(.+)$/);
      const key = m && names.find((n) => n.toLowerCase() === m[1].trim().toLowerCase().replace(/ /g, '_'));
      if (key) out[key] = coerce(props[key], m[2].trim());
    }
    if (Object.keys(out).length) return out;
  }
  const strings = Object.entries(props).filter(([, p]) => p.type === 'string' && !p.enum);
  for (const [k, p] of Object.entries(props)) if (p.default !== undefined) out[k] = p.default;
  const required = schema?.required || [];
  const target = strings.find(([k]) => required.includes(k)) || strings[0];
  if (target) out[target[0]] = t;
  return out;
}

const coerce = (p, v) => (p?.type === 'number' || p?.type === 'integer' ? Number(v) : p?.type === 'boolean' ? /^(true|yes|1)$/i.test(v) : v);

const specCache = new Map();

export async function frankensteinSpec(assistant) {
  const id = assistant?.provider?.kind === 'frankenstein' && assistant.provider.monsterId;
  if (!id) return null;
  if (!specCache.has(id)) specCache.set(id, await F.getMonster(id));
  return specCache.get(id);
}

async function* frankensteinRun(assistant, input, { signal, onRun }) {
  const id = assistant.provider.monsterId;
  const spec = await frankensteinSpec(assistant);
  const leaves = leavesOf(spec);
  const inputs = mapInputs(spec.inputs, input);
  // The backend rejects a run with a required input missing, so ask for it first.
  for (const key of spec.inputs?.required || []) {
    if (inputs[key] !== undefined && inputs[key] !== '') continue;
    const p = spec.inputs.properties?.[key] || {};
    let answer;
    const got = new Promise((resolve, reject) => {
      answer = resolve;
      signal?.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })), { once: true });
    });
    const options = p.enum ? ` (${p.enum.join(', ')})` : '';
    yield { type: 'question', text: `I also need the ${human(key).toLowerCase()}: ${p.description || ''}${options}`, reply: async (text) => answer(text) };
    inputs[key] = coerce(p, String(await got).trim());
  }
  const run = await F.startRun(id, inputs);
  onRun?.(run.id);

  // Funnel SSE updates into this generator.
  const queue = [];
  let wake = null, failure = null;
  const push = (x) => { queue.push(x); wake?.(); };
  const close = F.watchRun(run.id, push, (err) => { failure = err; wake?.(); });
  const onAbort = () => { failure = Object.assign(new Error('Aborted'), { name: 'AbortError' }); close(); wake?.(); };
  signal?.addEventListener('abort', onAbort, { once: true });

  const started = new Set(), finished = new Set();
  const asked = new Set();
  const stepOf = (leaf, i) => ({ id: leaf.id, label: leaf.note || human(leaf.id), kind: kindForLimb(leaf.limb), limb: leaf.limb, index: i, total: leaves.length });
  try {
    push(run);
    for (;;) {
      while (!queue.length && !failure) await new Promise((r) => { wake = r; });
      wake = null;
      if (failure) throw failure;
      const r = queue.shift();
      // Finished leaves, from the run log.
      for (const ev of r.log || []) {
        if (!ev.step || finished.has(ev.step) || !['done', 'skipped', 'declined', 'error'].includes(ev.event)) continue;
        const i = leaves.findIndex((l) => l.id === ev.step);
        if (i < 0) continue;
        if (!started.has(ev.step)) { started.add(ev.step); yield { type: 'step', step: stepOf(leaves[i], i) }; }
        finished.add(ev.step);
        yield { type: 'stepDone', step: stepOf(leaves[i], i), outcome: ev.event, usage: ev.usage || null };
      }
      // Running: the unfinished leaves of the first unfinished top-level step.
      if (['queued', 'running', 'waiting', 'needs_input', 'needs_confirmation'].includes(r.status)) {
        const next = leaves.find((l) => !finished.has(l.id));
        if (next) {
          for (const [i, l] of leaves.entries()) {
            if (l.top === next.top && !finished.has(l.id) && !started.has(l.id)) {
              started.add(l.id);
              yield { type: 'step', step: stepOf(l, i) };
            }
          }
        }
      }
      if (r.status === 'needs_input' && r.needs_input && !asked.has('q:' + r.needs_input.message)) {
        asked.add('q:' + r.needs_input.message);
        yield { type: 'question', text: r.needs_input.message, reply: (text) => F.answer(r.id, text) };
      }
      if (r.status === 'needs_confirmation' && r.confirm && !asked.has('c:' + r.confirm.message)) {
        asked.add('c:' + r.confirm.message);
        yield { type: 'confirm', text: r.confirm.message, credits: r.confirm.credits, decide: (ok) => F.confirm(r.id, ok) };
      }
      if (r.status === 'failed' || r.status === 'blocked') throw new Error(r.error || `run ${r.status}`);
      if (r.status === 'completed') {
        const out = r.output || {};
        const speechUsage = (r.log || []).filter((e) => e.step === 'speech' && e.usage).reduce((a, e) => addUsage(a, e.usage), {});
        if (out.speech) yield { type: 'speech', text: out.speech, audio: F.artifactUrl(out.speech_audio_url), usage: speechUsage };
        const report = String(out.report || out.speech || '(no report)');
        for (const w of report.match(/\s*\S+\s*/g) || []) {
          if (signal?.aborted) throw Object.assign(new Error('Aborted'), { name: 'AbortError' });
          await new Promise((res) => setTimeout(res, 12));
          yield { type: 'token', text: w };
        }
        return;
      }
    }
  } finally {
    close();
    signal?.removeEventListener('abort', onAbort);
  }
}

// Questions that clearly belong to another topic get redirected, keeping each
// assistant within its subject.
export function offTopic(assistant, input) {
  const scores = scoreTopics(input);
  const own = scores[assistant.theme] || 0;
  let best = null, bestScore = 0;
  for (const [id, s] of Object.entries(scores)) if (id !== assistant.theme && s > bestScore) { best = id; bestScore = s; }
  if (assistant.theme === 'generic') return null;
  return own === 0 && bestScore >= 1 ? best : null;
}

async function* redirect(assistant, other, { signal }) {
  const step = { id: 'dept', label: 'Checking this is my department', kind: 'think', index: 0, total: 1 };
  yield { type: 'step', step };
  await new Promise((r, j) => { const t = setTimeout(r, 1100); signal?.addEventListener('abort', () => { clearTimeout(t); j(Object.assign(new Error('Aborted'), { name: 'AbortError' })); }); });
  yield { type: 'stepDone', step };
  const text = `That sounds like a question for the **${themeOf(other).label}** monster, not me — I only do **${themeOf(assistant.theme).label.toLowerCase()}**. Ask it in the **Laboratory** and the doctor will fetch the right one (or build it).`;
  for (const w of text.match(/\s*\S+\s*/g)) {
    await new Promise((r) => setTimeout(r, 30));
    if (signal?.aborted) throw Object.assign(new Error('Aborted'), { name: 'AbortError' });
    yield { type: 'token', text: w };
  }
}

export function respond(assistant, input, opts) {
  // Scripted monsters need this guard; forged ones were told to stay on topic.
  const scripted = (assistant.provider?.kind || 'scripted') === 'scripted';
  const other = scripted && !input.trim().startsWith('{') ? offTopic(assistant, input) : null;
  if (other) return redirect(assistant, other, opts);
  const run = PROVIDERS[assistant.provider?.kind] || PROVIDERS.scripted;
  return run(assistant, input, opts);
}
