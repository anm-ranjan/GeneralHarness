import test from 'node:test'
import assert from 'node:assert/strict'
import { modelFamily, providerFamily, vendorInitials } from './providerFamily.js'

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

test('OpenRouter authors pick their vendor logo, including aliases', () => {
  assert.equal(modelFamily('deepseek/deepseek-v4'), 'deepseek')
  assert.equal(modelFamily('meta-llama/llama-4-maverick'), 'meta')
  assert.equal(modelFamily('~moonshotai/kimi-latest'), 'moonshotai')
  assert.equal(modelFamily('bytedance-seed/seed-2'), 'bytedance')
  assert.equal(modelFamily('aion-labs/aion-2'), '')
  assert.equal(modelFamily('gemini-3-pro'), 'google')
})

test('vendors without a logo get initials', () => {
  assert.equal(vendorInitials('aion-labs/aion-2'), 'AL')
  assert.equal(vendorInitials('sakana/x'), 'SA')
  assert.equal(vendorInitials(''), '')
})
