import assert from 'node:assert/strict'
import test from 'node:test'

import { fetchEventsBefore, stageItemsForEvents } from './olderEvents.js'
import { initialState, reducer } from './context/appReducer.js'

const ev = (type, data = {}) => ({ type, data })

test('fetches everything before the window in pages', async () => {
  const all = Array.from({ length: 2500 }, (_, i) => ev('status', { i }))
  const calls = []
  const events = await fetchEventsBefore(2300, async (offset, limit) => {
    calls.push([offset, limit])
    return { events: all.slice(offset, offset + limit) }
  })
  assert.deepEqual(calls, [[0, 1000], [1000, 1000], [2000, 300]])
  assert.equal(events.length, 2300)
  assert.equal(events[2299].data.i, 2299)
})

test('older events become history items without touching the live state', () => {
  const current = { ...initialState, currentSessionId: 'ses_1', isRunning: true, plan: [{ content: 'live' }] }
  const items = stageItemsForEvents([
    ev('user_message', { text: 'How it started' }),
    ev('assistant_message', { markdown: 'First answer' }),
    ev('run_finished', { reason: 'completed' }),
  ], current)
  assert.deepEqual(items.map(i => i.type), ['user_message', 'assistant_message'])
  assert.equal(items[0].text, 'How it started')
  assert.ok(items.every(i => i.historical))
  assert.equal(current.isRunning, true)
  assert.deepEqual(current.plan, [{ content: 'live' }])
})

test('earlier items go in front, and a stale load is ignored', () => {
  const state = {
    ...initialState,
    currentSessionId: 'ses_1',
    eventWindowOffset: 300,
    stageItems: [{ type: 'user_message', text: 'latest', _id: 'b' }],
  }
  const older = [{ type: 'user_message', text: 'first', _id: 'a' }]
  const next = reducer(state, { type: 'PREPEND_STAGE_ITEMS', payload: { sessionId: 'ses_1', items: older, offset: 300 } })
  assert.deepEqual(next.stageItems.map(i => i.text), ['first', 'latest'])
  assert.equal(next.eventWindowOffset, 0)

  const switched = reducer(state, { type: 'PREPEND_STAGE_ITEMS', payload: { sessionId: 'ses_2', items: older, offset: 300 } })
  assert.equal(switched, state)
})
