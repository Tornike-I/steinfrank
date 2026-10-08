// localStorage persistence for conversations, their living creatures and settings.
const KEY = 'stitchwick-lab.v1';

const defaults = () => ({
  conversations: [],
  currentId: null,
  monsters: [], // topic assistants: see monsters/registry.js
  labCreatures: [], // wandering counterparts in the laboratory
  mode: 'lab', // 'lab' | 'monsters'
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
      mode: data.mode === 'monsters' ? 'monsters' : 'lab',
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
