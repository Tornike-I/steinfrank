// localStorage persistence for conversations, their living creatures and settings.
const KEY = 'stitchwick-lab.v1';

const defaults = () => ({
  conversations: [],
  currentId: null,
  monsters: [], // topic assistants: see monsters/registry.js
  labCreatures: [], // wandering counterparts in the laboratory
  mode: 'lab', // 'lab' | 'monsters' | 'library'
  libraryView: 'roam', // 'roam' | 'cards'
  selectedMonster: null,
  settings: { threshold: 8, sound: false },
});

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defaults();
    const data = JSON.parse(raw);
    const d = defaults();
    return {
      conversations: Array.isArray(data.conversations) ? data.conversations : [],
      currentId: data.currentId ?? null,
      monsters: Array.isArray(data.monsters) ? data.monsters : [],
      labCreatures: Array.isArray(data.labCreatures) ? data.labCreatures : [],
      mode: ['monsters', 'library'].includes(data.mode) ? data.mode : 'lab',
      libraryView: data.libraryView === 'cards' ? 'cards' : 'roam',
      selectedMonster: data.selectedMonster ?? null,
      deletedPresets: Array.isArray(data.deletedPresets) ? data.deletedPresets : [],
      // Sound always starts muted, regardless of the last session.
      settings: { ...d.settings, ...(data.settings || {}), sound: false },
    };
  } catch {
    return defaults();
  }
}

export const state = load();

// Anything left mid-stream by a refresh is treated as stopped.
for (const c of [...state.conversations, ...state.monsters.map((m) => ({ messages: m.chat || [] }))]) {
  c.creatures ??= [];
  for (const m of c.messages) {
    if (m.status === 'streaming' || m.status === 'pending' || m.status === 'queued') {
      m.status = 'stopped';
    }
  }
}

let saveTimer = 0;
export function save(immediate = false) {
  clearTimeout(saveTimer);
  const write = () => {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* quota or private mode */ }
  };
  if (immediate) write(); else saveTimer = setTimeout(write, 150);
}

window.addEventListener('beforeunload', () => save(true));

export const current = () => state.conversations.find((c) => c.id === state.currentId) || null;

// Several tabs of the app share one localStorage key. Without syncing, a
// stale tab's periodic save would overwrite assistants another tab created.
// So adopt other tabs' writes: assistants merge by id (the copy with the
// longer chat wins); this tab keeps its own lab creatures and UI mode.
const listeners = new Set();
export const onExternalChange = (fn) => listeners.add(fn);

window.addEventListener('storage', (ev) => {
  if (ev.key !== KEY || !ev.newValue) return;
  let other;
  try { other = JSON.parse(ev.newValue); } catch { return; }
  let changed = false;
  for (const m of other.monsters || []) {
    const mine = state.monsters.find((x) => x.id === m.id);
    if (!mine) { state.monsters.push(m); changed = true; }
    else if ((m.chat?.length || 0) > (mine.chat?.length || 0) && !mine._live) { Object.assign(mine, m); changed = true; }
  }
  for (const id of other.deletedPresets || []) if (!state.deletedPresets?.includes(id)) (state.deletedPresets ||= []).push(id);
  if (other.settings?.threshold) state.settings.threshold = other.settings.threshold;
  if (changed) for (const fn of listeners) fn();
});
