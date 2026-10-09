// Client for the Frankenstein API (steinfrank/frankenstein, FastAPI).
// In dev, Vite proxies /frank → http://localhost:8000 (see vite.config.js);
// set VITE_FRANK_URL to call another host directly (CORS allows localhost).
const BASE = (import.meta.env.VITE_FRANK_URL || '/frank').replace(/\/$/, '');

// Connection status, refreshed by watchHealth(). When Frankenstein is not
// reachable, new monsters are built in scripted (offline) mode.
export const backend = { connected: false, checked: false };
// ?offline=1 (or localStorage "stitchwick-lab.offline" = "1") forces scripted
// mode, for demos without the backend or API keys.
const FORCE_OFFLINE = new URLSearchParams(location.search).has('offline') || (() => {
  try { return localStorage.getItem('stitchwick-lab.offline') === '1'; } catch { return false; }
})();
export function watchHealth(onChange, every = 15000) {
  const check = async () => {
    const ok = FORCE_OFFLINE ? false : await health();
    const changed = ok !== backend.connected || !backend.checked;
    backend.connected = ok;
    backend.checked = true;
    if (changed) onChange?.(ok);
  };
  check();
  setInterval(check, every);
}

async function req(path, { method = 'GET', body, signal, timeout } = {}) {
  const ctrl = new AbortController();
  const t = timeout ? setTimeout(() => ctrl.abort(), timeout) : null;
  signal?.addEventListener('abort', () => ctrl.abort(), { once: true });
  try {
    const res = await fetch(BASE + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const detail = typeof data === 'object' && data?.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : String(data || res.statusText);
      const e = new Error(`Frankenstein ${res.status}: ${detail.slice(0, 300)}`);
      e.status = res.status;
      throw e;
    }
    return data;
  } catch (err) {
    if (err.name === 'AbortError' && signal?.aborted) throw err;
    if (err.name === 'AbortError') throw new Error('Frankenstein did not answer in time.');
    throw err;
  } finally {
    if (t) clearTimeout(t);
  }
}

export const health = () => req('/monsters', { timeout: 2500 }).then(() => true, () => false);
export const forge = (description, signal) => req('/forge', { method: 'POST', body: { description }, signal });
export const publish = (id, voice = true) => req(`/monsters/${encodeURIComponent(id)}/publish`, { method: 'POST', body: { voice } });
export const getMonster = (id) => req(`/monsters/${encodeURIComponent(id)}`);
export const listMonsters = () => req('/monsters');
export const startRun = (id, inputs) => req(`/monsters/${encodeURIComponent(id)}/runs`, { method: 'POST', body: { inputs } });
export const getRun = (runId) => req(`/runs/${runId}`);
export const voiceSession = (id) => req(`/monsters/${encodeURIComponent(id)}/voice-session`);
export const answer = (runId, text) => req(`/runs/${runId}/inputs`, { method: 'POST', body: { answer: text } });
export const confirm = (runId, approve) => req(`/runs/${runId}/confirm`, { method: 'POST', body: { approve } });
export const artifactUrl = (url) => (url && url.startsWith('/') ? BASE + url : url);

const TERMINAL = new Set(['completed', 'failed', 'blocked']);

// Server-sent run updates. Closes itself on a terminal status (EventSource
// would otherwise reconnect forever). Returns a close function.
export function watchRun(runId, onUpdate, onError) {
  const es = new EventSource(`${BASE}/runs/${runId}/events`);
  let closed = false;
  const close = () => { if (!closed) { closed = true; es.close(); } };
  es.onmessage = (ev) => {
    let run;
    try { run = JSON.parse(ev.data); } catch { return; }
    onUpdate(run);
    if (TERMINAL.has(run.status)) close();
  };
  es.onerror = () => {
    if (closed) return;
    // The stream dropped: keep following the run by polling instead.
    es.close();
    (async () => {
      while (!closed) {
        try {
          const run = await req(`/runs/${runId}`);
          onUpdate(run);
          if (TERMINAL.has(run.status)) closed = true;
        } catch (err) {
          closed = true;
          onError?.(err);
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
  };
  return close;
}
