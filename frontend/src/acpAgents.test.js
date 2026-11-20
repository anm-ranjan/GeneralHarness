import test from 'node:test'
import assert from 'node:assert/strict'
import { acpAgentEnabled, acpKey, acpLabel, isAcpProvider } from './acpAgents.js'
import { providerFamily } from './providerFamily.js'
import { commandUnavailableReason } from './commands.js'
import { modeLabel } from './sessionLabels.js'
import { providerLabel } from './usage.js'
import { waitingPhrase } from './waitingPhrase.js'

const agents = [{ provider: 'acp:antigravity', key: 'antigravity', label: 'Antigravity' }]

test('ACP providers are recognised by their prefix', () => {
  assert.equal(isAcpProvider('acp:antigravity'), true)
  assert.equal(isAcpProvider('claude-agent'), false)
  assert.equal(acpKey('acp:antigravity'), 'antigravity')
  assert.equal(acpKey('native'), '')
})

test('labels come from the backend list, else the key', () => {
  assert.equal(acpLabel('acp:antigravity', agents), 'Antigravity')
  assert.equal(acpLabel('acp:goose'), 'Goose')
  assert.equal(modeLabel({ provider: 'acp:antigravity' }), 'Antigravity')
  assert.equal(providerLabel('acp:antigravity'), 'Antigravity')
  assert.equal(waitingPhrase(0, 'acp:antigravity'), 'Thinking with Antigravity…')
})

test('an agent is enabled only when the backend lists it', () => {
  assert.equal(acpAgentEnabled({ acpAgents: agents }, 'acp:antigravity'), true)
  assert.equal(acpAgentEnabled({ acpAgents: [] }, 'acp:antigravity'), false)
  assert.equal(acpAgentEnabled({}, 'acp:antigravity'), false)
})

test('Antigravity shows the Google logo, following its model when known', () => {
  assert.equal(providerFamily('acp:antigravity'), 'google')
  assert.equal(providerFamily('acp:antigravity', 'gemini-4-neon'), 'google')
  assert.equal(providerFamily('acp:other'), '')
})

test('commands listed for "acp" apply to every ACP agent', () => {
  assert.equal(commandUnavailableReason({ providers: ['native', 'acp'] }, 'acp:antigravity'), null)
  assert.equal(commandUnavailableReason({ providers: ['native'] }, 'acp:antigravity'), 'Only for native sessions')
})
