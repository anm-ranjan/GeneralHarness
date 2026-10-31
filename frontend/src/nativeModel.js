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
