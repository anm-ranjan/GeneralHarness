import test from 'node:test'
import assert from 'node:assert/strict'
import { followNewOutput, isNearBottom } from './stageScroll.js'

test('near the bottom within the threshold', () => {
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 600, clientHeight: 400 }), true)
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 530, clientHeight: 400 }), true)
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 400 }), false)
})

test('new output follows only a reader at the bottom', () => {
  const base = { sessionChanged: false, replaying: false, ownMessage: false, atBottom: false }
  assert.equal(followNewOutput(base), 'notify')
  assert.equal(followNewOutput({ ...base, atBottom: true }), 'bottom')
  assert.equal(followNewOutput({ ...base, ownMessage: true }), 'bottom')
  assert.equal(followNewOutput({ ...base, sessionChanged: true }), 'bottom')
  assert.equal(followNewOutput({ ...base, replaying: true }), 'bottom')
})
