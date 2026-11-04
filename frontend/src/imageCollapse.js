// Collapsed/expanded state for transcript images, remembered per browser.
// Keys are stable image identities, so the state survives reloads and
// re-renders of the same message.

const KEY = 'myharness:imageCollapse'
const MAX_KEYS = 500

const LOCAL_IMAGE = '/api/files/image?'

// Stable identity: the file path for local images (ignoring the cache-busting
// version and the backend origin apiUrl() may prefix), otherwise the URL
// without its query string.
export function imageKey(src) {
  const raw = String(src || '')
  // Inline data URLs have no stable identity worth storing; they use the default.
  if (raw.startsWith('data:')) return ''
  const local = raw.indexOf(LOCAL_IMAGE)
  if (local !== -1) {
    const path = new URLSearchParams(raw.slice(local + LOCAL_IMAGE.length).split('#')[0]).get('path')
    if (path) return `file:${path}`
  }
  return raw.split(/[?#]/)[0]
}

// Label shown on a collapsed image: its alt text, else the file name.
export function imageLabel(src, alt) {
  if (alt && String(alt).trim()) return String(alt).trim()
  const key = imageKey(src).replace(/^file:/, '')
  const name = key.split(/[\\/]/).filter(Boolean).pop()
  try {
    return name ? decodeURIComponent(name) : 'image'
  } catch {
    return name || 'image'
  }
}

// Stored as { key: true (collapsed) | false (expanded) }: only images the user
// toggled are recorded; everything else follows the caller's default.
function read() {
  try {
    const value = JSON.parse(globalThis.localStorage?.getItem(KEY) || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch {
    return {}
  }
}

// Messages replayed from an opened session default to collapsed; images that
// arrive live in the current view default to expanded.
export function isImageCollapsed(src, defaultCollapsed = false) {
  const key = imageKey(src)
  const state = key ? read()[key] : undefined
  return typeof state === 'boolean' ? state : Boolean(defaultCollapsed)
}

export function setImageCollapsed(src, collapsed) {
  const key = imageKey(src)
  if (!key) return
  const state = read()
  delete state[key]
  state[key] = Boolean(collapsed)
  const keys = Object.keys(state)
  for (const old of keys.slice(0, Math.max(0, keys.length - MAX_KEYS))) delete state[old]
  try {
    globalThis.localStorage?.setItem(KEY, JSON.stringify(state))
  } catch { /* Storage may be unavailable. */ }
}
