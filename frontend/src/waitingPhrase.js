// What the live margin line says while the model is working and no tool is
// running. It starts at "Thinking…" and moves through these as the wait drags on,
// driven by how long it has been since the last sign of life, so each tool return
// starts again at "Thinking…". Deterministic, so the line never flickers back and forth.
const WAITING_PHRASES = [
  'Computing',
  'Still thinking',
  'Working on it',
  'Mulling it over',
  'Piecing it together',
  'Still at it',
]
const FIRST_PHRASE_MS = 4000
const PHRASE_MS = 5000

export function waitingPhrase(idleMs) {
  const idle = Number.isFinite(idleMs) && idleMs > 0 ? idleMs : 0
  if (idle < FIRST_PHRASE_MS) return 'Thinking…'
  const step = Math.floor((idle - FIRST_PHRASE_MS) / PHRASE_MS)
  return `${WAITING_PHRASES[step % WAITING_PHRASES.length]}…`
}
