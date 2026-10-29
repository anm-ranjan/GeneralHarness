const KEY = 'myharness.lastSession'
export function lastSessionId() {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}
export function rememberSession(id) {
  try { localStorage.setItem(KEY, id) } catch { /* Storage may be unavailable. */ }
}
export function isRowAction(target) {
  return !!target.closest('button, input, textarea, select, a')
}

const PREFS_KEY = 'myharness.sidebar'
// Sidebar visibility and Projects/Activity view, remembered per browser.
export function sidebarPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')
    return { open: raw.open !== false, view: raw.view === 'activity' ? 'activity' : 'projects' }
  } catch {
    return { open: true, view: 'projects' }
  }
}
export function saveSidebarPrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)) } catch { /* Storage may be unavailable. */ }
}
