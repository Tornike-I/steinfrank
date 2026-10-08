// Topic assistants. Each created monster is a persistent, separately
// configured assistant dedicated to one broad topic:
//   { id, theme (topic id), name, seed, instructions,
//     provider: { kind: 'scripted', model, endpoint }, chat: [...], createdAt }
// `theme` is the topic id (it also drives the monster's look).
// There is at most one assistant per topic.
import { state, save } from '../core/store.js';
import { uid, randomSeed, Rng } from '../core/rng.js';
import { buildMonster } from './monsterGen.js';
import { specOf, themeOf, makeName } from './themes.js';
import { LIMB_FOR_KIND } from './limbParts.js';
import { WORKFLOWS } from '../workflows/workflows.js';

const RENAMED = { dossier: 'meetings', writer: 'writing', numbers: 'math' };

// Migrate saves from earlier prototypes: drop old presets, rename topics.
state.monsters = state.monsters.filter((m) => !m.preset).map((m) => {
  const theme = RENAMED[m.theme] || m.theme;
  return { ...m, theme, limbs: m.limbs || scriptedLimbs(theme), provider: m.provider || { kind: 'scripted' } };
});

export const allMonsters = () => [...state.monsters].sort((a, b) => a.createdAt - b.createdAt);
export const getMonster = (id) => state.monsters.find((m) => m.id === id) || null;
export const findByTopic = (topic) => state.monsters.find((m) => m.theme === topic) || null;

// A not-yet-saved assistant for a topic (saved only once creation completes).
export function draftAssistant(topic) {
  const seed = randomSeed();
  const th = themeOf(topic);
  return {
    id: uid('mon'),
    theme: topic,
    name: makeName(topic, new Rng(seed)),
    seed,
    instructions: th.instructions,
    provider: { kind: 'scripted', model: null, endpoint: null },
    limbs: scriptedLimbs(topic),
    chat: [],
  };
}

// Offline monsters still get a body that matches their scripted workflow.
export function scriptedLimbs(topic) {
  const wf = WORKFLOWS[topic] || WORKFLOWS.generic;
  const steps = wf.steps({ ...wf.parse('example'), input: 'example' });
  return [...new Set(steps.map((s) => LIMB_FOR_KIND[s.kind]).filter(Boolean))];
}

export function commitAssistant(draft) {
  if (findByTopic(draft.theme)) return findByTopic(draft.theme);
  const a = { ...draft, createdAt: Date.now() };
  state.monsters.push(a);
  save();
  return a;
}

// Build the 3D monster for an assistant (or a bare seed + topic).
// `limbs` (Frankenstein limb names) become sewn-on body parts.
export function buildFor(defOrSeed, themeId, limbs) {
  if (typeof defOrSeed === 'number') return buildMonster(defOrSeed, { ...(themeId ? specOf(themeId) : {}), limbs: limbs || [] });
  return buildMonster(defOrSeed.seed, { ...specOf(defOrSeed.theme), limbs: defOrSeed.limbs || [] });
}
