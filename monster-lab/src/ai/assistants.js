// Provider layer for topic assistants. Every assistant carries its own
// `provider` config, so each topic monster can later point at a different
// model / endpoint / system prompt (`assistant.instructions`).
//
// A provider returns an async iterable of workflow events:
//   { type: 'step', step } | { type: 'stepDone', step } | { type: 'token', text }
// and must honour `signal` (throw AbortError) and throw on failure.
import { runWorkflow } from '../workflows/engine.js';
import { scoreTopics, themeOf } from '../monsters/themes.js';

const PROVIDERS = {
  // Simulated: themed steps + scripted answer.
  scripted: (assistant, input, opts) => runWorkflow(assistant, input, opts),
  // Example for later:
  // http: async function* (assistant, input, { signal, history }) {
  //   const res = await fetch(assistant.provider.endpoint, { method: 'POST', signal,
  //     body: JSON.stringify({ model: assistant.provider.model, system: assistant.instructions, messages: history }) });
  //   ...yield { type: 'token', text } per chunk
  // },
};

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
  const other = offTopic(assistant, input);
  if (other) return redirect(assistant, other, opts);
  const run = PROVIDERS[assistant.provider?.kind] || PROVIDERS.scripted;
  return run(assistant, input, opts);
}
