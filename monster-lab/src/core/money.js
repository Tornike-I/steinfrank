// Dollar amounts as people read them: two significant digits below a cent.
export function usd(v) {
  if (!v) return '$0';
  if (v < 0.01) return `$${Number(v.toPrecision(2))}`;
  return `$${v.toFixed(v < 1 ? 3 : 2)}`;
}

// The raw usage behind a dollar amount, for tooltips.
export function usageDetail(u) {
  if (!u) return '';
  const parts = [];
  if (u.llm_tokens) parts.push(`${Math.round(u.llm_tokens)} tokens`);
  if (u.tts_chars) parts.push(`${Math.round(u.tts_chars)} voice characters`);
  if (u.credits) parts.push(`${Math.round(u.credits)} Sokosumi credits`);
  if (u.images) parts.push(`${u.images} images`);
  return parts.join(' · ');
}

export function addUsage(a = {}, b = {}) {
  const out = { ...a };
  for (const k of ['llm_tokens', 'credits', 'tts_chars', 'images', 'usd']) out[k] = (a[k] || 0) + (b?.[k] || 0);
  return out;
}
