import test from 'node:test'
import assert from 'node:assert/strict'
import { modelFamily, providerFamily } from './providerFamily.js'

test('model ids map to their vendor', () => {
  assert.equal(modelFamily('openai/gpt-6.1-terra'), 'openai')
  assert.equal(modelFamily('anthropic/claude-sonnet-5-5'), 'anthropic')
  assert.equal(modelFamily('claude-opus-5-5'), 'anthropic')
  assert.equal(modelFamily('mistral/large'), '')
  assert.equal(modelFamily(), '')
})

test('Codex and Claude providers have a fixed vendor; Native follows its model', () => {
  assert.equal(providerFamily('codex-app-server', 'anything'), 'openai')
  assert.equal(providerFamily('claude-agent'), 'anthropic')
  assert.equal(providerFamily('native', 'openai/gpt-6.1-terra'), 'openai')
  assert.equal(providerFamily('native', 'local/llama'), '')
})
