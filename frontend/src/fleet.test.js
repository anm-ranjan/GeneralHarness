import test from 'node:test'
import assert from 'node:assert/strict'

import {
  describeHostStatus,
  findSelfHost,
  hostNeedsAttention,
  hostIdMismatch,
  hostStatus,
  hostStorageKey,
  normalizeFleet,
  resolveActiveHost,
  summarizeOtherHosts,
} from './fleet.js'

const FLEET = {
  hosts: [
    { id: 'laptop', label: 'Laptop', url: 'http://127.0.0.1:8420/', self: true },
    { id: 'workstation', label: 'Workstation', url: 'http://127.0.0.1:8421' },
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
      { id: 'laptop', url: 'http://a' },
      { id: 'laptop', url: 'http://dup' },
      { id: '', url: 'http://b' },
      { id: 'nourl' },
      { id: 'workstation', url: 'http://c' },
    ],
  })
  assert.deepEqual(hosts.map(h => h.id), ['laptop', 'workstation'])
  assert.equal(hosts[0].url, 'http://a')
})

test('normalizeFleet treats a lone host as no fleet at all', () => {
  assert.deepEqual(normalizeFleet({ hosts: [{ id: 'laptop', url: 'http://a' }] }), [])
  assert.deepEqual(normalizeFleet({ hosts: [] }), [])
  assert.deepEqual(normalizeFleet(null), [])
  assert.deepEqual(normalizeFleet({ hosts: 'nonsense' }), [])
})

test('normalizeFleet falls back to the id when a label is missing', () => {
  const [host] = normalizeFleet({
    hosts: [{ id: 'workstation', url: 'http://a' }, { id: 'laptop', url: 'http://b' }],
  })
  assert.equal(host.label, 'workstation')
})

test('resolveActiveHost prefers the saved host', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(resolveActiveHost(hosts, 'workstation').id, 'workstation')
})

test('resolveActiveHost falls back to self when the saved host is gone', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(resolveActiveHost(hosts, 'retired-box').id, 'laptop')
  assert.equal(resolveActiveHost(hosts, '').id, 'laptop')
})

test('resolveActiveHost returns null without a fleet', () => {
  assert.equal(resolveActiveHost([], 'laptop'), null)
})

test('findSelfHost returns the machine serving the page', () => {
  const hosts = normalizeFleet(FLEET)
  assert.equal(findSelfHost(hosts).id, 'laptop')
})

// Without a self entry there is no host known to answer, so the caller has to
// leave the user where they are rather than guess at a recovery target.
test('findSelfHost returns null when no host claims to be self', () => {
  const hosts = normalizeFleet({
    hosts: [
      { id: 'workstation', label: 'Workstation', url: 'http://127.0.0.1:8421' },
      { id: 'other', label: 'Other', url: 'http://127.0.0.1:8422' },
    ],
  })
  assert.equal(findSelfHost(hosts), null)
  assert.equal(findSelfHost([]), null)
  assert.equal(findSelfHost(undefined), null)
})

test('hostStorageKey namespaces state per host', () => {
  assert.equal(hostStorageKey('workstation', 'draft:s1'), 'myharness:workstation:draft:s1')
  assert.notEqual(hostStorageKey('laptop', 'draft:s1'), hostStorageKey('workstation', 'draft:s1'))
})

test('hostStorageKey falls back to the single-host namespace', () => {
  assert.equal(hostStorageKey('', 'draft:s1'), 'myharness:local:draft:s1')
})

test('hostStatus reads a successful poll', () => {
  const status = hostStatus({ ok: true, running: 2, waiting_approval: 1 })
  assert.deepEqual(status, { online: true, running: 2, waitingApproval: 1, reportedId: '' })
})

test('hostStatus treats a failed or malformed poll as offline', () => {
  assert.deepEqual(hostStatus(null), { online: false, running: 0, waitingApproval: 0, reportedId: '' })
  assert.deepEqual(hostStatus({ ok: false }), { online: false, running: 0, waitingApproval: 0, reportedId: '' })
  assert.deepEqual(
    hostStatus({ ok: true, running: 'x', waiting_approval: -3 }),
    { online: true, running: 0, waitingApproval: 0, reportedId: '' },
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
    laptop: { online: true, running: 5, waitingApproval: 2 },
    workstation: { online: true, running: 1, waitingApproval: 3 },
  }
  assert.deepEqual(
    summarizeOtherHosts(hosts, statuses, 'laptop'),
    { waitingApproval: 3, running: 1, offline: 0 },
  )
})

test('summarizeOtherHosts counts unpolled hosts as offline', () => {
  const hosts = normalizeFleet(FLEET)
  assert.deepEqual(
    summarizeOtherHosts(hosts, {}, 'laptop'),
    { waitingApproval: 0, running: 0, offline: 1 },
  )
})

// ── host id divergence ────────────────────────────────────────────────
//
// Host ids namespace saved per-host state and are how machines refer to each
// other, so configs that disagree are a real misconfiguration -- and a silent
// one, since each machine works fine alone.

test('hostStatus carries the id a host reports for itself', () => {
  const status = hostStatus({ ok: true, running: 0, waiting_approval: 0, host_id: 'workstation' })
  assert.equal(status.reportedId, 'workstation')
  assert.equal(hostStatus({ ok: false }).reportedId, '')
})

test('hostIdMismatch reports the id a host actually calls itself', () => {
  const host = { id: 'Workstation', label: 'Workstation', url: 'http://a' }
  const status = hostStatus({ ok: true, host_id: 'workstation' })
  assert.equal(hostIdMismatch(host, status), 'workstation')
})

test('hostIdMismatch stays quiet when the ids agree', () => {
  const host = { id: 'workstation', label: 'Workstation', url: 'http://a' }
  assert.equal(hostIdMismatch(host, hostStatus({ ok: true, host_id: 'workstation' })), '')
})

test('hostIdMismatch stays quiet when there is nothing to compare', () => {
  const host = { id: 'workstation', label: 'Workstation', url: 'http://a' }
  // A peer with no fleet configured, or one predating the check, reports ''.
  assert.equal(hostIdMismatch(host, hostStatus({ ok: true })), '')
  assert.equal(hostIdMismatch(host, hostStatus({ ok: true, host_id: '   ' })), '')
  // An offline host has nothing to say about itself.
  assert.equal(hostIdMismatch(host, hostStatus({ ok: false })), '')
  assert.equal(hostIdMismatch(host, null), '')
})
