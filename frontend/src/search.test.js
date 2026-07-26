import assert from 'node:assert/strict'
import test from 'node:test'

import { groupHitsBySession, fieldLabel, findAnchorEventIndex } from './search.js'

test('groups hits by session preserving first-seen order', () => {
  const hits = [
    { session_id: 'a', snippet: 'one' },
    { session_id: 'b', snippet: 'two' },
    { session_id: 'a', snippet: 'three' },
  ]
  const groups = groupHitsBySession(hits)
  assert.equal(groups.length, 2)
  assert.equal(groups[0].session.session_id, 'a')
  assert.equal(groups[0].items.length, 2)
  assert.equal(groups[1].session.session_id, 'b')
  assert.equal(groups[1].items.length, 1)
})

test('handles empty or missing hit lists', () => {
  assert.deepEqual(groupHitsBySession([]), [])
  assert.deepEqual(groupHitsBySession(undefined), [])
})

test('maps known field labels and falls back to raw field', () => {
  assert.equal(fieldLabel('prompt'), 'Prompt')
  assert.equal(fieldLabel('tool_result'), 'Tool result')
  assert.equal(fieldLabel('mystery'), 'mystery')
})

test('findAnchorEventIndex prefers the closest item at or before the target', () => {
  const items = [
    { eventIndex: 10 },
    { type: 'status' },
    { eventIndex: 14 },
    { eventIndex: 20 },
  ]
  assert.equal(findAnchorEventIndex(items, 14), 14)
  assert.equal(findAnchorEventIndex(items, 17), 14)
  assert.equal(findAnchorEventIndex(items, 99), 20)
})

test('findAnchorEventIndex falls back to the first item after the target', () => {
  const items = [{ eventIndex: 30 }, { eventIndex: 25 }]
  assert.equal(findAnchorEventIndex(items, 5), 25)
  assert.equal(findAnchorEventIndex([{ type: 'status' }], 5), null)
  assert.equal(findAnchorEventIndex([], 5), null)
})
