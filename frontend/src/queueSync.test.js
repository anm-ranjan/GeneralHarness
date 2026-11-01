import assert from 'node:assert/strict'
import test from 'node:test'

import { initialState, reducer } from './context/appReducer.js'

const stale = {
  ...initialState,
  currentSessionId: 'ses_codex',
  queuedMessages: [{ id: 'qmsg_1', text: 'Correction to my previous message', imageCount: 0, attachmentCount: 0, createdAt: '' }],
}

test("the server's queue replaces a stale chip for the open thread", () => {
  const next = reducer(stale, { type: 'SYNC_QUEUE', payload: { sessionId: 'ses_codex', queue: [], steering: [] } })
  assert.deepEqual(next.queuedMessages, [])
  assert.deepEqual(next.pendingSteering, [])
})

test('a queue fetched for a thread no longer open is ignored', () => {
  const next = reducer(stale, { type: 'SYNC_QUEUE', payload: { sessionId: 'ses_claude', queue: [], steering: [] } })
  assert.equal(next, stale)
})

test("server items are normalised like queue_updated's", () => {
  const next = reducer(stale, {
    type: 'SYNC_QUEUE',
    payload: { sessionId: 'ses_codex', queue: [{ id: 'q2', text: 'next', images: [], attachments: [{}], created_at: 't' }], steering: [] },
  })
  assert.deepEqual(next.queuedMessages, [{ id: 'q2', text: 'next', imageCount: 0, attachmentCount: 1, createdAt: 't' }])
})
