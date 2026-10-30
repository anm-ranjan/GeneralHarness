import test from 'node:test'
import assert from 'node:assert/strict'

import {
  bridgeAction,
  bridgeStateLabel,
  describeActivity,
  normalizeBridgeHosts,
  stopConfirmation,
} from './bridge.js'

test('normalizeBridgeHosts keeps well-formed rows and defaults unknown states to unreachable', () => {
  const hosts = normalizeBridgeHosts({
    hosts: [
      { id: 'jarvis', label: 'Jarvis', state: 'online', running: 2, waiting_approval: 1 },
      { id: 'odd', state: 'bogus' },
      { label: 'no id', state: 'off' },
    ],
  })
  assert.deepEqual(hosts.map(host => host.id), ['jarvis', 'odd'])
  assert.equal(hosts[0].running, 2)
  assert.equal(hosts[0].waitingApproval, 1)
  assert.equal(hosts[1].state, 'unreachable')
  assert.equal(hosts[1].label, 'odd')
})

test('normalizeBridgeHosts tolerates a missing payload', () => {
  assert.deepEqual(normalizeBridgeHosts(null), [])
  assert.deepEqual(normalizeBridgeHosts({}), [])
})

test('each state offers the matching action, and unreachable offers none', () => {
  assert.equal(bridgeAction({ state: 'online' }).kind, 'stop')
  assert.equal(bridgeAction({ state: 'off' }).kind, 'start')
  assert.equal(bridgeAction({ state: 'unreachable' }), null)
  assert.equal(bridgeStateLabel('off'), 'Off')
  assert.equal(bridgeStateLabel('nonsense'), 'Unreachable')
})

test('stopConfirmation names what would be interrupted and forces only then', () => {
  const idle = stopConfirmation({ label: 'Jarvis', running: 0, waitingApproval: 0 })
  assert.equal(idle.force, false)
  assert.match(idle.message, /stops the backend on Jarvis/)

  const busy = stopConfirmation({ label: 'Jarvis', running: 2, waitingApproval: 1 })
  assert.equal(busy.force, true)
  assert.match(busy.message, /1 waiting for approval and 2 running/)
  assert.equal(describeActivity({ running: 0, waitingApproval: 0 }), '')
})
