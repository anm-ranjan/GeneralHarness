import test from 'node:test'
import assert from 'node:assert/strict'
import { formatCost, formatReset, formatTokens, usageTotals } from './usage.js'
import { modelFromInput, perMillion } from './nativeModel.js'

test('token and cost formatting', () => {
  assert.equal(formatTokens(950), '950')
  assert.equal(formatTokens(17356), '17k')
  assert.equal(formatTokens(1_250_000), '1.3M')
  assert.equal(formatCost(0), '$0.00')
  assert.equal(formatCost(0.0271), '$0.027')
  assert.equal(formatCost(0.004), '$0.0040')
  assert.equal(formatCost(12.345), '$12.35')
})

test('reset times read as a countdown within a day', () => {
  const now = Date.UTC(2026, 9, 31, 12, 0, 0)
  assert.equal(formatReset(now / 1000 + 3 * 3600 + 12 * 60, now), 'in 3h 12m')
  assert.equal(formatReset(now / 1000 + 600, now), 'in 10m')
  assert.equal(formatReset(now / 1000 - 1, now), 'now')
})

test('plan-covered use is kept apart from charged cost', () => {
  const totals = usageTotals([
    { cost_usd: 0.5, cost_kind: 'reported', input_tokens: 100, output_tokens: 10 },
    { cost_usd: 2, cost_kind: 'equivalent', input_tokens: 1000, output_tokens: 50 },
    { cost_usd: null, cost_kind: 'estimated', input_tokens: 1, output_tokens: 1 },
  ])
  assert.deepEqual(totals, { charged: 0.5, equivalent: 2, tokens: 1162 })
})

test('model ids are taken from OpenRouter URLs', () => {
  assert.equal(modelFromInput('https://openrouter.ai/openai/gpt-6.1-terra?endpoint=6a8480a0'), 'openai/gpt-6.1-terra')
  assert.equal(modelFromInput('  deepseek/deepseek-v4 '), 'deepseek/deepseek-v4')
  assert.equal(perMillion('0.0000012'), '$1.20')
  assert.equal(perMillion('0'), 'free')
  assert.equal(perMillion(undefined), '—')
})
