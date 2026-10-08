// Workflow runner. A workflow is a list of steps followed by a response:
//
//   { parse(input) → ctx,
//     steps(ctx) → [{ id, label, kind, duration: [min, max], run?(ctx, signal) }],
//     respond(ctx) → markdown string }
//
// `kind` (gather | read | think | compute | write) is what the monster acts out
// while the step runs. Simulated steps just wait; a real step provides
// `run(ctx, signal)` (e.g. fetch an API) and may stash results on ctx for
// respond() to use. Events yielded:
//   { type: 'step', step }   { type: 'stepDone', step }   { type: 'token', text }
import { WORKFLOWS } from './workflows.js';

const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); reject(abortError()); }, { once: true });
  });

function abortError() {
  const e = new Error('Aborted');
  e.name = 'AbortError';
  return e;
}

export async function* runWorkflow(def, input, { signal } = {}) {
  const wf = WORKFLOWS[def.theme] || WORKFLOWS.generic;
  const ctx = { input, def, ...wf.parse(input) };
  if (/\bfail\b/i.test(input)) ctx.fail = true;
  const steps = wf.steps(ctx);
  for (let i = 0; i < steps.length; i++) {
    const step = { ...steps[i], index: i, total: steps.length };
    yield { type: 'step', step };
    const [a, b] = step.duration || [900, 1600];
    if (step.run) await step.run(ctx, signal);
    else await sleep(a + Math.random() * (b - a), signal);
    if (ctx.fail && i === Math.floor(steps.length / 2)) throw new Error(`${def.name} tripped over its own tail mid-workflow. (Simulated failure.)`);
    yield { type: 'stepDone', step };
  }
  const text = wf.respond(ctx);
  const tokens = text.match(/\s*\S+\s*/g) || [];
  for (let i = 0; i < tokens.length; i++) {
    const n = 1 + (Math.random() < 0.4 ? 1 : 0);
    const chunk = tokens.slice(i, i + n).join('');
    i += n - 1;
    await sleep(22 + Math.random() * 40 + (/[.!?:]\s*$/.test(chunk) ? 90 : 0), signal);
    yield { type: 'token', text: chunk };
  }
}
