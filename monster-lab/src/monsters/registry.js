// Monsters built in the lab. There is one persistent assistant per topic:
//   { id, theme (topic id), topic?, name, seed, job, jobs, instructions,
//     provider: { kind: 'scripted' | 'frankenstein', ... }, chat: [...], createdAt }
// `theme` is one of the built-in topics (themes.js); it drives the look and
// voice. A job no built-in topic recognises gets its own topic, `theme:
// 'generic'` plus `topic: { label, keywords }`, and later jobs that share its
// keywords go to the same monster. `jobs` lists every job the monster was
// asked to do; related jobs are learned rather than built again.
import { state, save } from '../core/store.js';
import { uid, randomSeed, Rng } from '../core/rng.js';
import { buildMonster } from './monsterGen.js';
import { specOf, themeOf, makeName, matchTheme } from './themes.js';
import { LIMB_FOR_KIND } from './limbParts.js';
import { WORKFLOWS } from '../workflows/workflows.js';

const RENAMED = { dossier: 'meetings', writer: 'writing', numbers: 'math' };

export const allMonsters = () => [...state.monsters].sort((a, b) => a.createdAt - b.createdAt);
export const getMonster = (id) => state.monsters.find((m) => m.id === id) || null;
export const findByTopic = (topic) => state.monsters.find((m) => m.theme === topic) || null;

// --- topics ------------------------------------------------------------------
// Words that say nothing about what a job is about.
const STOP = new Set(`a an the and or but if of to in on at by for from with about into over under than then
this that these those it its is are was were be been being am do does did done have has had can could will would
shall should may might must i me my mine we us our you your he she they them their what which who whom whose when
where why how all any each every some no not only just also very really please thanks hey hi hello
make build create give get tell show find check help let want need like know see look keep send set use run
bot bots monster monsters assistant agent tool app thing something someone stuff job jobs task new
whether every daily weekly always one two three four five six seven eight nine ten first last next
me good best better quick quickly simple short long list lists`.split(/\s+/));

const stem = (w) => w.replace(/(ingly|edly|ings|ing|ers|er|ies|es|ed|ly|s)$/, (x) => (w.length - x.length >= 3 ? (x === 'ies' ? 'y' : '') : x));

// Significant words of a job, in order, with their stems.
export function jobWords(text) {
  const words = String(text).toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  const out = [];
  for (const w of words) {
    if (w.length < 3 || STOP.has(w) || /^\d+$/.test(w)) continue;
    const s = stem(w);
    if (!out.some((o) => o.stem === s)) out.push({ word: w, stem: s });
  }
  return out;
}

// A new topic named after a job no built-in topic recognises.
export function customTopic(job) {
  const words = jobWords(job);
  const label = words.slice(0, 3).map((w) => w.word).join(' ') || 'odd jobs';
  return { label: label[0].toUpperCase() + label.slice(1), keywords: words.map((w) => w.stem) };
}

// How strongly a job belongs to a custom topic: shared keywords, relative to
// the shorter of the two lists (so "is this shop legit" matches "check
// whether an online shop is a scam").
function overlap(keywords, job) {
  const mine = new Set(keywords);
  const theirs = jobWords(job).map((w) => w.stem);
  if (!mine.size || !theirs.length) return 0;
  const shared = theirs.filter((k) => mine.has(k)).length;
  return shared >= 2 ? 1 : shared / Math.min(mine.size, theirs.length);
}

// Which topic a job belongs to, and the monster that already covers it:
//   { theme, topic (custom topics only), label, existing }
export function topicForJob(job) {
  const theme = matchTheme(job);
  const newest = (list) => list.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))[0] || null;
  if (theme !== 'generic') {
    return { theme, topic: null, label: themeOf(theme).label, existing: newest(state.monsters.filter((m) => m.theme === theme)) };
  }
  let best = null, score = 0;
  for (const m of state.monsters) {
    if (m.theme !== 'generic' || !m.topic) continue;
    const sc = overlap(m.topic.keywords, job);
    if (sc > score) { score = sc; best = m; }
  }
  if (best && score >= 0.5) return { theme, topic: best.topic, label: best.topic.label, existing: best };
  const topic = customTopic(job);
  return { theme, topic, label: topic.label, existing: null };
}

// A monster takes on a related job. Returns false if it already knew it.
export function learnJob(def, job) {
  def.jobs ||= def.job ? [def.job] : [];
  const words = jobWords(job).map((w) => w.stem).join(' ');
  if (def.jobs.some((j) => jobWords(j).map((w) => w.stem).join(' ') === words)) return false;
  def.jobs.push(job);
  if (def.topic) def.topic.keywords = [...new Set([...def.topic.keywords, ...jobWords(job).map((w) => w.stem)])];
  save();
  return true;
}

// A not-yet-saved assistant for a topic (saved only once creation completes).
export function draftAssistant(topic, job = '', custom = null) {
  const seed = randomSeed();
  const th = themeOf(topic);
  return {
    id: uid('mon'),
    theme: topic,
    name: makeName(topic, new Rng(seed)),
    seed,
    form: 2,
    job,
    jobs: job ? [job] : [],
    ...(custom ? { topic: { ...custom, keywords: [...custom.keywords] } } : {}),
    instructions: th.instructions,
    provider: { kind: 'scripted', model: null, endpoint: null },
    limbs: scriptedLimbs(topic),
    chat: [],
  };
}

// Monsters published on the server join every visitor's Library; ids are fixed so a deletion sticks.
export function adoptServerMonsters(cards) {
  const known = new Set(state.monsters.map((m) => m.provider?.monsterId).filter(Boolean));
  const gone = new Set(state.deletedMonsters || []);
  let added = 0;
  for (const card of cards) {
    const id = `srv-${card.id}`;
    if (known.has(card.id) || gone.has(id) || getMonster(id)) continue;
    const theme = matchTheme(`${card.name} ${card.purpose}`);
    if (theme !== 'generic' && findByTopic(theme)) continue;
    const custom = theme === 'generic'
      ? { label: customTopic(card.name).label, keywords: customTopic(`${card.name} ${card.purpose}`).keywords }
      : null;
    const seed = [...card.id].reduce((h, c) => Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0, 2166136261);
    commitAssistant({
      ...draftAssistant(theme, card.purpose, custom),
      id,
      seed,
      name: makeName(theme, new Rng(seed)),
      provider: { kind: 'frankenstein', monsterId: card.id, name: card.name, purpose: card.purpose, inputs: card.inputs, voice: card.has_voice },
      limbs: card.limbs,
    });
    known.add(card.id);
    added++;
  }
  return added;
}

// Offline monsters still get a body that matches their scripted workflow.
export function scriptedLimbs(topic) {
  const wf = WORKFLOWS[topic] || WORKFLOWS.generic;
  const steps = wf.steps({ ...wf.parse('example'), input: 'example' });
  return [...new Set(steps.map((s) => LIMB_FOR_KIND[s.kind]).filter(Boolean))];
}

export function commitAssistant(draft) {
  const a = { ...draft, createdAt: Date.now() };
  state.monsters.push(a);
  save();
  return a;
}

// Build the 3D monster for an assistant (or a bare seed + topic).
// `limbs` (Frankenstein limb names) become sewn-on body parts.
// `form` 2+ enables the wider variety of body plans and sizes.
export function buildFor(defOrSeed, themeId, limbs, form = 1) {
  if (typeof defOrSeed === 'number') return buildMonster(defOrSeed, { ...(themeId ? specOf(themeId) : {}), limbs: limbs || [], variety: form >= 2 });
  return buildMonster(defOrSeed.seed, { ...specOf(defOrSeed.theme), limbs: defOrSeed.limbs || [], variety: (defOrSeed.form || 1) >= 2 });
}

// What the monster is for, as shown in tabs and headers: its topic ("Weather
// monster"), never one particular job ("Prague weather bot").
export const titleOf = (def) => {
  if (def?.topic?.label) return `${def.topic.label} monster`;
  if (def?.theme && def.theme !== 'generic') return `${themeOf(def.theme).label} monster`;
  return def?.title || def?.provider?.name || `${themeOf(def?.theme).label} monster`;
};
export const topicLabelOf = (def) => def?.topic?.label || themeOf(def?.theme).label;

// Migrate saves from earlier prototypes: drop old presets, rename topics,
// give job-built monsters their job list and custom topic. (Last, so the
// topic helpers above are initialised.)
state.monsters = state.monsters.filter((m) => !m.preset).map((m) => {
  const theme = RENAMED[m.theme] || m.theme;
  const out = { ...m, theme, limbs: m.limbs || scriptedLimbs(theme), provider: m.provider || { kind: 'scripted' } };
  out.jobs ||= m.job ? [m.job] : [];
  if (theme === 'generic' && !out.topic && m.job) out.topic = customTopic(m.job);
  return out;
});
