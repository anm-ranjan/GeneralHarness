import test from 'node:test'
import assert from 'node:assert/strict'
import { copyText } from './clipboard.js'

function setup(t, { writeText, result = true } = {}) {
  const calls = []
  const range = {}
  const selection = {
    rangeCount: 1,
    getRangeAt: () => ({ cloneRange: () => range }),
    removeAllRanges: () => calls.push('clear selection'),
    addRange: value => { assert.equal(value, range); calls.push('restore selection') },
  }
  const textarea = {
    style: {},
    setAttribute() {},
    focus: () => calls.push('focus textarea'),
    select: () => calls.push('select text'),
    remove: () => calls.push('remove textarea'),
  }
  const document = {
    activeElement: { focus: () => calls.push('restore focus') },
    getSelection: () => selection,
    createElement: () => textarea,
    body: { appendChild: () => calls.push('append textarea') },
    execCommand: command => { assert.equal(command, 'copy'); calls.push('copy'); return result },
  }
  for (const [key, value] of Object.entries({ navigator: { clipboard: writeText ? { writeText } : undefined }, document })) {
    const original = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { configurable: true, value })
    t.after(() => {
      if (original) Object.defineProperty(globalThis, key, original)
      else delete globalThis[key]
    })
  }
  return { calls, textarea, document }
}

test('uses the Clipboard API when available', async t => {
  let copied
  const { calls } = setup(t, { writeText: async text => { copied = text } })
  await copyText('Project / Task / Session — session_id: abc')
  assert.equal(copied, 'Project / Task / Session — session_id: abc')
  assert.deepEqual(calls, [])
})

for (const denied of [false, true]) {
  test(`falls back when Clipboard API is ${denied ? 'denied' : 'unavailable'} and restores focus/selection`, async t => {
    const { calls, textarea } = setup(t, {
      writeText: denied ? async () => { throw new Error('NotAllowedError') } : undefined,
    })
    await copyText('Session — session_id: abc')
    assert.equal(textarea.value, 'Session — session_id: abc')
    assert.deepEqual(calls, ['append textarea', 'focus textarea', 'select text', 'copy', 'remove textarea', 'restore focus', 'clear selection', 'restore selection'])
  })
}

test('reports failure when fallback is blocked and still cleans up', async t => {
  const { calls } = setup(t, { result: false })
  await assert.rejects(copyText('Session'), /Clipboard copy was blocked/)
  assert.ok(calls.includes('remove textarea'))
  assert.ok(calls.includes('restore focus'))
})

test('cleans up when the fallback throws', async t => {
  const { calls, document } = setup(t)
  document.execCommand = () => { throw new Error('Copy unavailable') }
  await assert.rejects(copyText('Session'), /Copy unavailable/)
  assert.ok(calls.includes('remove textarea'))
  assert.ok(calls.includes('restore focus'))
})
