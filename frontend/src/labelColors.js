// Stable label colours are selected from theme-owned tokens. The backend can
// continue exposing task ids while the frontend presents them as labels.
export const LABEL_COLOR_COUNT = 6
export const LABEL_COLORS = ['blue', 'violet', 'teal', 'amber', 'rose', 'indigo']

export function labelColorIndex(labelId) {
  let hash = 2166136261
  for (const char of String(labelId || '')) {
    hash ^= char.codePointAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0) % LABEL_COLOR_COUNT
}

export function labelColorStyle(labelColorOrId) {
  const explicit = LABEL_COLORS.indexOf(labelColorOrId)
  const index = explicit >= 0 ? explicit : labelColorIndex(labelColorOrId)
  return { '--label-color': `var(--color-label-${index + 1})` }
}
