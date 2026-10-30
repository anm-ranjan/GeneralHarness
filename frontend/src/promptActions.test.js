import test from 'node:test'
import assert from 'node:assert/strict'
import { attachmentPayloads, canRemovePrompt, promptAttachments } from './promptActions.js'

const respond = (body, type = 'image/png', ok = true, status = 200) => ({
  ok, status, blob: async () => new Blob([body], { type }),
})

test('stored attachments come back as composer payloads', async () => {
  const seen = []
  const fetchImpl = async url => { seen.push(url); return respond('abc') }
  const out = await attachmentPayloads([
    { url: '/api/sessions/s/attachments/a.png', name: 'a.png', mime: 'image/png' },
    { url: '/api/sessions/s/attachments/b.txt', filename: 'b.txt' },
  ], fetchImpl)
  assert.deepEqual(seen, ['/api/sessions/s/attachments/a.png', '/api/sessions/s/attachments/b.txt'])
  assert.deepEqual(out[0], { data: 'data:image/png;base64,YWJj', mime: 'image/png', name: 'a.png' })
  // No stored mime: the response's type is used; the filename stands in for a name.
  assert.equal(out[1].mime, 'image/png')
  assert.equal(out[1].name, 'b.txt')
})

test('a failed download aborts instead of silently dropping the attachment', async () => {
  const fetchImpl = async () => respond('', 'text/plain', false, 404)
  await assert.rejects(
    attachmentPayloads([{ url: '/x', name: 'gone.png' }], fetchImpl),
    /Could not load gone\.png again \(404\)/,
  )
})

test('no attachments, or ones without a url, produce no payloads and no requests', async () => {
  const fetchImpl = async () => { throw new Error('should not fetch') }
  assert.deepEqual(await attachmentPayloads([], fetchImpl), [])
  assert.deepEqual(await attachmentPayloads(undefined, fetchImpl), [])
  assert.deepEqual(await attachmentPayloads([{ name: 'no-url' }], fetchImpl), [])
})

test('large files are encoded without overflowing the call stack', async () => {
  const big = 'x'.repeat(300_000)
  const [payload] = await attachmentPayloads([{ url: '/big', name: 'big.bin', mime: 'application/octet-stream' }], async () => respond(big, 'application/octet-stream'))
  assert.equal(Buffer.from(payload.data.split(',')[1], 'base64').toString(), big)
})

test('prompt attachments fall back to the older images field', () => {
  assert.deepEqual(promptAttachments({ attachments: [{ url: 'a' }], images: [{ url: 'b' }] }), [{ url: 'a' }])
  assert.deepEqual(promptAttachments({ attachments: [], images: [{ url: 'b' }] }), [{ url: 'b' }])
  assert.deepEqual(promptAttachments({}), [])
  assert.deepEqual(promptAttachments(null), [])
})

test('only native sessions can have a prompt removed; Codex and Claude keep their own history', () => {
  assert.ok(canRemovePrompt('native'))
  assert.ok(!canRemovePrompt('codex-app-server'))
  assert.ok(!canRemovePrompt('claude-agent'))
  assert.ok(!canRemovePrompt(''))
  assert.ok(!canRemovePrompt(undefined))
})
