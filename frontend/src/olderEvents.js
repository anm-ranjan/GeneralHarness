import { handleSessionEvent } from './eventHandlers.js'
import { initialState, reducer } from './context/appReducer.js'

// The server sends a thread's newest events (about 200, widened back to the
// prompt that opened the oldest turn); these helpers load what came before.

const PAGE = 1000

/** Fetch events [0, end) in pages. `fetchPage(offset, limit)` returns `{ events }`. */
export async function fetchEventsBefore(end, fetchPage) {
  const events = []
  for (let offset = 0; offset < end; offset += PAGE) {
    const page = await fetchPage(offset, Math.min(PAGE, end - offset))
    const chunk = page.events || []
    events.push(...chunk)
    if (chunk.length === 0) break
  }
  return events.slice(0, end)
}

/**
 * Stage items for finished, older events, built on a scratch copy of the
 * state so the live thread (a running turn, its plan, agents, queue) is
 * left alone. Every older turn is complete, so all of it replays as history.
 */
export function stageItemsForEvents(events, current) {
  let scratch = {
    ...initialState,
    currentSessionId: current.currentSessionId,
    currentProvider: current.currentProvider,
    currentWorkspaceRoot: current.currentWorkspaceRoot,
  }
  const stateRef = { get current() { return scratch } }
  const dispatch = action => { scratch = reducer(scratch, action) }
  handleSessionEvent(
    { type: 'session_loaded', data: { meta: { status: 'idle' }, events, event_offset: 0 } },
    dispatch,
    stateRef,
  )
  return scratch.stageItems
}
