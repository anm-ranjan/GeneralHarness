import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

// QueuedMessages and Composer are siblings remounted per session. With the same
// key React keeps stale copies on every session switch, so queued chips multiply.
test('per-session composer siblings use distinct keys', () => {
  const src = fs.readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const queue = src.match(/<QueuedMessages key=(.*?) \/>/)
  const composer = src.match(/<Composer key=(.*?) \/>/)
  assert.ok(queue && composer, 'both components are keyed')
  assert.notEqual(queue[1], composer[1])
})
