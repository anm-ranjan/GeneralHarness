import test from 'node:test'
import assert from 'node:assert/strict'
import { agentIsLive, agentLabel, agentStatus, agentStatusLabel, canInterruptAgent, isAgentStateEvent, sortedAgents } from './agents.js'

test('lifecycle actions imply a status, Codex thread statuses are normalised', () => {
  assert.equal(agentStatus({ action: 'started' }), 'running')
  assert.equal(agentStatus({ action: 'spawned' }), 'pending')
  assert.equal(agentStatus({ action: 'spawned', status: 'running' }), 'running')
  assert.equal(agentStatus({ action: 'completed' }), 'completed')
  assert.equal(agentStatus({ action: 'interrupting' }), 'interrupting')
  assert.equal(agentStatus({ action: 'error' }), 'failed')
  assert.equal(agentStatus({ action: 'status', status: 'active' }), 'running')
  assert.equal(agentStatus({ action: 'status', status: 'idle' }), 'idle')
  assert.equal(agentStatus({ action: 'tool_call', text: 'Reading file' }), undefined)
})

test('only events about an agent\'s state belong in the panel', () => {
  assert.ok(isAgentStateEvent({ action: 'spawned' }))
  assert.ok(isAgentStateEvent({ action: 'status', status: 'active' }))
  assert.ok(!isAgentStateEvent({ action: 'tool_call', text: 'x' }))
  assert.ok(!isAgentStateEvent({ action: 'plan', items: [] }))
  assert.ok(!isAgentStateEvent({}))
})

test('agents sort root first, live before finished, then by path', () => {
  const agents = {
    a: { agent_path: '/root/zeta', status: 'completed' },
    b: { agent_path: '/root/beta', status: 'running' },
    c: { agent_path: '/root', status: 'running' },
    d: { agent_path: '/root/alpha', status: 'completed' },
    e: { agent_path: '/root/gamma', status: 'pending' },
  }
  assert.deepEqual(sortedAgents(agents).map(a => a.agent_path), ['/root', '/root/beta', '/root/gamma', '/root/alpha', '/root/zeta'])
})

test('only live native child agents can be stopped from the UI', () => {
  assert.ok(canInterruptAgent({ provider: 'native', agent_path: '/root/worker', status: 'running' }))
  assert.ok(!canInterruptAgent({ provider: 'native', agent_path: '/root', status: 'running' }))
  assert.ok(!canInterruptAgent({ provider: 'native', agent_path: '/root/worker', status: 'completed' }))
  assert.ok(!canInterruptAgent({ provider: 'native', agent_path: '/root/worker', status: 'interrupting' }))
  assert.ok(!canInterruptAgent({ provider: 'codex-app-server', agent_path: '/root/worker', status: 'running' }))
  assert.ok(!canInterruptAgent(null))
})

test('labels and status words read plainly', () => {
  assert.equal(agentLabel({ nickname: 'Reviewer', agent_path: '/root/r' }), 'Reviewer')
  assert.equal(agentLabel({ agent_path: '/root/r' }), '/root/r')
  assert.equal(agentLabel({ agent_id: 'thr_1' }), 'thr_1')
  assert.equal(agentLabel(null), 'agent')
  assert.equal(agentStatusLabel('running'), 'working')
  assert.equal(agentStatusLabel('interrupted'), 'stopped')
  assert.equal(agentStatusLabel('odd'), 'odd')
  assert.ok(agentIsLive({ status: 'pending' }))
  assert.ok(!agentIsLive({ status: 'failed' }))
})

test('a Codex thread going idle after completing stays done', async () => {
  const { reducer, initialState } = await import('./context/appReducer.js')
  let state = { ...initialState }
  state = reducer(state, { type: 'UPSERT_AGENT', payload: { agent_id: 'child', agent_path: '/root/review', status: 'completed' } })
  state = reducer(state, { type: 'UPSERT_AGENT', payload: { agent_id: 'child', status: 'idle' } })
  assert.equal(state.agentsById.child.status, 'completed')
})
