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

const RENAMED = { dossier: 'meetings', writer: 'writing', numbers: 'math' };

// Migrate saves from earlier prototypes: drop old presets, rename topics.
state.monsters = state.monsters.filter((m) => !m.preset).map((m) => ({ ...m, theme: RENAMED[m.theme] || m.theme }));

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
    chat: [],
  };
}

export function commitAssistant(draft) {
  if (findByTopic(draft.theme)) return findByTopic(draft.theme);
  const a = { ...draft, createdAt: Date.now() };
  state.monsters.push(a);
  save();
  return a;
}

// Build the 3D monster for an assistant (or a bare seed + topic).
export function buildFor(defOrSeed, themeId) {
  if (typeof defOrSeed === 'number') return buildMonster(defOrSeed, themeId ? specOf(themeId) : {});
  return buildMonster(defOrSeed.seed, specOf(defOrSeed.theme));
}
