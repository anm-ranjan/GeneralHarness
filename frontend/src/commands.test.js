import test from 'node:test'
import assert from 'node:assert/strict'
import { commandInsertText, commandUnavailableReason } from './commands.js'

test('usage without placeholders is inserted as is', () => {
  assert.equal(commandInsertText('/compact'), '/compact')
  assert.equal(commandInsertText('/chdir --reset'), '/chdir --reset')
})

test('usage with a placeholder stops before it and leaves a space', () => {
  assert.equal(commandInsertText('/chdir <directory>'), '/chdir ')
  assert.equal(commandInsertText('/approve <always_ask|shell_only|auto_approve>'), '/approve ')
})

test('provider restrictions are reported only when they apply', () => {
  const compact = { providers: ['native'] }
  assert.equal(commandUnavailableReason(compact, 'native'), null)
  assert.equal(commandUnavailableReason(compact, 'codex-app-server'), 'Only for native sessions')
  assert.equal(commandUnavailableReason(compact, undefined), null)
  assert.equal(commandUnavailableReason({}, 'native'), null)
  assert.equal(commandUnavailableReason({ providers: ['native', 'claude-agent'] }, 'codex-app-server'),
    'Only for native, Claude sessions')
})
