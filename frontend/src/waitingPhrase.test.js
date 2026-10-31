import test from 'node:test'
import assert from 'node:assert/strict'
import { waitingPhrase } from './waitingPhrase.js'

test('the waiting phrase starts at Thinking and rotates as the wait drags on', () => {
  assert.equal(waitingPhrase(0), 'Thinking…')
  assert.equal(waitingPhrase(3999), 'Thinking…')
  assert.equal(waitingPhrase(4000), 'Computing…')
  assert.equal(waitingPhrase(8999), 'Computing…')
  assert.equal(waitingPhrase(9000), 'Still thinking…')
  assert.equal(waitingPhrase(14000), 'Working on it…')
  const seen = new Set()
  for (let s = 4; s < 4 + 5 * 6; s += 5) seen.add(waitingPhrase(s * 1000))
  assert.equal(seen.size, 6)
  assert.equal(waitingPhrase(4000 + 5000 * 6), 'Computing…')
})

test('bad idle values fall back to Thinking', () => {
  assert.equal(waitingPhrase(-500), 'Thinking…')
  assert.equal(waitingPhrase(NaN), 'Thinking…')
  assert.equal(waitingPhrase(undefined), 'Thinking…')
})

test('the first phrase names Claude or Codex; native just thinks', () => {
  assert.equal(waitingPhrase(0, 'claude-agent'), 'Thinking with Claude…')
  assert.equal(waitingPhrase(1000, 'codex-app-server'), 'Thinking with Codex…')
  assert.equal(waitingPhrase(0, 'native'), 'Thinking…')
  assert.equal(waitingPhrase(4000, 'claude-agent'), 'Computing…')
})
