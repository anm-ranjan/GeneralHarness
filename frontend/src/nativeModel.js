// Native Settings input helpers.

/** "openai/gpt-6.1-terra", or the same taken from an OpenRouter model page URL. */
export function modelFromInput(value) {
  const text = String(value || '').trim()
  const match = text.match(/^(?:https?:\/\/)?(?:www\.)?openrouter\.ai\/([^/?#\s]+\/[^/?#\s]+)/i)
  return match ? decodeURIComponent(match[1]) : text
}

/** Per-token USD string from OpenRouter -> "$1.20" per million tokens. */
export function perMillion(perToken) {
  const value = Number(perToken)
  if (perToken == null || perToken === '' || !Number.isFinite(value)) return '—'
  const million = value * 1e6
  if (million === 0) return 'free'
  return `$${million < 0.1 ? million.toFixed(3) : million.toFixed(2)}`
}

// Providers shown before "Show all": the fastest that can call tools.
export const TOP_PROVIDERS = 4

/** Tool-capable endpoints by recent throughput, fastest first; the rest after. */
export function rankEndpoints(endpoints) {
  const speed = e => (typeof e.throughput === 'number' ? e.throughput : -1)
  return [...endpoints].sort((a, b) => (b.tools - a.tools) || (speed(b) - speed(a)))
}

/** The top few by throughput plus every provider the user picked, in rank order. */
export function visibleEndpoints(ranked, chosen, showAll) {
  if (showAll) return ranked
  const top = new Set(ranked.filter(e => e.tools).slice(0, TOP_PROVIDERS).map(e => e.tag))
  return ranked.filter(e => top.has(e.tag) || chosen.includes(e.tag))
}
