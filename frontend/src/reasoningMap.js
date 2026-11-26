// Reasoning map (Native Settings, local endpoint only): which chat-template
// variable each thinking level sets, and to what. Mirrors
// utils.template_value / build_local_reasoning_kwargs in the backend.
export const MAP_LEVELS = ['low', 'medium', 'high']

/** "true"/"false" -> boolean, numbers -> number, anything else -> text. */
export function templateValue(text) {
  const value = String(text ?? '').trim()
  if (/^(true|false)$/i.test(value)) return value.toLowerCase() === 'true'
  if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(value)) return Number(value)
  return value
}

/** The chat_template_kwargs a level would send, or null when it sends nothing. */
export function kwargsFor(map, level) {
  const variable = String(map?.variable ?? '').trim()
  const raw = String(map?.[level] ?? '').trim()
  if (!variable || !raw) return null
  return { [variable]: templateValue(raw) }
}

/** One line per level, e.g. 'low → {"reasoning_effort":"low"}'. */
export function mapPreview(map) {
  return MAP_LEVELS.map(level => {
    const kwargs = kwargsFor(map, level)
    return `${level} → ${kwargs ? JSON.stringify(kwargs) : 'nothing (template default)'}`
  })
}

export const VARIABLE_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
