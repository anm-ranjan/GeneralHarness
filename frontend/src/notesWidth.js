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

// Width of the whole thread block (thread + notes), when the notes' outer
// edge has been dragged. Null means the automatic width.
export const BLOCK_WIDTH_KEY = 'myharness.threadBlockWidth'
export const BLOCK_MIN = NOTES_MIN + COLUMN_GAP + THREAD_MIN

export function clampBlockWidth(width, stageWidth) {
  return Math.round(Math.min(Math.max(BLOCK_MIN, stageWidth), Math.max(BLOCK_MIN, width)))
}

export function readNotesWidth(storage = globalThis.localStorage, key = NOTES_WIDTH_KEY) {
  try {
    const value = Number(storage?.getItem(key))
    return Number.isFinite(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

export function saveNotesWidth(width, storage = globalThis.localStorage, key = NOTES_WIDTH_KEY) {
  try {
    if (width == null) storage?.removeItem(key)
    else storage?.setItem(key, String(Math.round(width)))
  } catch {
    // Private mode or a full store: the width just isn't remembered.
  }
}
