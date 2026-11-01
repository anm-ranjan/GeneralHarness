// Width of the margin-notes column, when the user has dragged it. Null means
// the automatic width (CSS: 17rem on a laptop, up to 22rem on wide screens).

export const NOTES_WIDTH_KEY = 'myharness.notesWidth'
export const NOTES_MIN = 180
// The thread keeps at least this much room, however wide the notes are dragged.
export const THREAD_MIN = 360
const COLUMN_GAP = 40

export function clampNotesWidth(width, blockWidth) {
  const max = Math.max(NOTES_MIN, blockWidth - COLUMN_GAP - THREAD_MIN)
  return Math.round(Math.min(max, Math.max(NOTES_MIN, width)))
}

export function readNotesWidth(storage = globalThis.localStorage) {
  try {
    const value = Number(storage?.getItem(NOTES_WIDTH_KEY))
    return Number.isFinite(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

export function saveNotesWidth(width, storage = globalThis.localStorage) {
  try {
    if (width == null) storage?.removeItem(NOTES_WIDTH_KEY)
    else storage?.setItem(NOTES_WIDTH_KEY, String(Math.round(width)))
  } catch {
    // Private mode or a full store: the width just isn't remembered.
  }
}
