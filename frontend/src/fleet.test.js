import test from 'node:test'
import assert from 'node:assert/strict'

import {
  describeHostStatus,
  hostNeedsAttention,
  hostStatus,
  hostStorageKey,
  normalizeFleet,
  resolveActiveHost,
  summarizeOtherHosts,
} from './fleet.js'

const FLEET = {
  hosts: [
    { id: 'mac', label: 'MacBook', url: 'http://127.0.0.1:8420/', self: true },
    { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421' },
  ],
}

test('normalizeFleet keeps well-formed hosts and strips trailing slashes', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(hosts.length, 2)
  assert.equal(hosts[0].url, 'http://127.0.0.1:8420')
  assert.equal(hosts[0].self, true)
  assert.equal(hosts[1].self, false)
})

test('normalizeFleet drops entries missing an id or url, and duplicates', () => {
  const hosts = normalizeFleet({
    hosts: [
      { id: 'mac', url: 'http://a' },
      { id: 'mac', url: 'http://dup' },
      { id: '', url: 'http://b' },
      { id: 'nourl' },
      { id: 'jarvis', url: 'http://c' },
    ],
  })
  assert.deepEqual(hosts.map(h => h.id), ['mac', 'jarvis'])
  assert.equal(hosts[0].url, 'http://a')
})

test('normalizeFleet treats a lone host as no fleet at all', () => {
  assert.deepEqual(normalizeFleet({ hosts: [{ id: 'mac', url: 'http://a' }] }), [])
  assert.deepEqual(normalizeFleet({ hosts: [] }), [])
  assert.deepEqual(normalizeFleet(null), [])
  assert.deepEqual(normalizeFleet({ hosts: 'nonsense' }), [])
})

test('normalizeFleet falls back to the id when a label is missing', () => {
  const [host] = normalizeFleet({
    hosts: [{ id: 'jarvis', url: 'http://a' }, { id: 'mac', url: 'http://b' }],
  })
  assert.equal(host.label, 'jarvis')
})

test('resolveActiveHost prefers the saved host', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(resolveActiveHost(hosts, 'jarvis').id, 'jarvis')
})

test('resolveActiveHost falls back to self when the saved host is gone', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(resolveActiveHost(hosts, 'retired-box').id, 'mac')
  assert.equal(resolveActiveHost(hosts, '').id, 'mac')
})

test('resolveActiveHost returns null without a fleet', () => {
  assert.equal(resolveActiveHost([], 'mac'), null)
})

test('hostStorageKey namespaces state per host', () => {
  assert.equal(hostStorageKey('jarvis', 'draft:s1'), 'myharness:jarvis:draft:s1')
  assert.notEqual(hostStorageKey('mac', 'draft:s1'), hostStorageKey('jarvis', 'draft:s1'))
})

test('hostStorageKey falls back to the single-host namespace', () => {
  assert.equal(hostStorageKey('', 'draft:s1'), 'myharness:local:draft:s1')
})

test('hostStatus reads a successful poll', () => {
  const status = hostStatus({ ok: true, running: 2, waiting_approval: 1 })
  assert.deepEqual(status, { online: true, running: 2, waitingApproval: 1 })
})

test('hostStatus treats a failed or malformed poll as offline', () => {
  assert.deepEqual(hostStatus(null), { online: false, running: 0, waitingApproval: 0 })
  assert.deepEqual(hostStatus({ ok: false }), { online: false, running: 0, waitingApproval: 0 })
  assert.deepEqual(
    hostStatus({ ok: true, running: 'x', waiting_approval: -3 }),
    { online: true, running: 0, waitingApproval: 0 },
  )
})

test('describeHostStatus leads with approvals, which block a run', () => {
  assert.equal(
    describeHostStatus({ online: true, running: 2, waitingApproval: 1 }),
    '1 waiting approval · 2 running',
  )
  assert.equal(describeHostStatus({ online: true, running: 1, waitingApproval: 0 }), '1 running')
  assert.equal(describeHostStatus({ online: true, running: 0, waitingApproval: 0 }), 'idle')
  assert.equal(describeHostStatus({ online: false }), 'unreachable')
})

test('hostNeedsAttention flags only reachable hosts with a blocked run', () => {
  assert.equal(hostNeedsAttention({ online: true, waitingApproval: 1 }), true)
  assert.equal(hostNeedsAttention({ online: true, waitingApproval: 0 }), false)
  assert.equal(hostNeedsAttention({ online: false, waitingApproval: 5 }), false)
})

test('summarizeOtherHosts ignores the host being viewed', () => {
  const hosts = normalizeFleet(FLEET)
  const statuses = {
    mac: { online: true, running: 5, waitingApproval: 2 },
    jarvis: { online: true, running: 1, waitingApproval: 3 },
  }
  assert.deepEqual(
    summarizeOtherHosts(hosts, statuses, 'mac'),
    { waitingApproval: 3, running: 1, offline: 0 },
  )
})

test('summarizeOtherHosts counts unpolled hosts as offline', () => {
  const hosts = normalizeFleet(FLEET)
  assert.deepEqual(
    summarizeOtherHosts(hosts, {}, 'mac'),
    { waitingApproval: 0, running: 0, offline: 1 },
  )
})
