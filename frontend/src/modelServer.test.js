import test from 'node:test'
import assert from 'node:assert/strict'
import {
  checkMessage, checkRequest, launchRequest, scriptError, scriptsQuery, serverForm, serverSummary,
} from './modelServer.js'

test('check endpoint request and message', () => {
  assert.match(checkRequest('gpu-box:8081').error, /base URL/)
  assert.deepEqual(checkRequest(' http://gpu-box.lan:8081/v1 ', ' q ').body, { base_url: 'http://gpu-box.lan:8081/v1', model: 'q' })
  assert.deepEqual(checkMessage({ ok: true, message: 'Responding' }), { ok: 'Responding' })
  assert.match(checkMessage({ ok: false, message: 'No q.', model_listed: false, models: ['a', 'b'] }).error, /It serves: a, b/)
  assert.equal(checkMessage(null), null)
})

test('the launch script is an absolute .sh path, never a command line', () => {
  assert.match(scriptError(''), /Enter the launch script/)
  assert.match(scriptError('C:\\llm\\serve.sh'), /Windows/)
  assert.match(scriptError('run-inkling.sh'), /absolute/)
  assert.match(scriptError('llama-server -m /m/q.gguf'), /absolute/)
  assert.match(scriptError('/usr/bin/llama-server -m q.gguf'), /\.sh file/)
  assert.equal(scriptError('/archive/models/run-inkling.sh'), '')
  assert.equal(scriptError('~/llm/serve.sh'), '')
})

test('launch request and form', () => {
  assert.match(launchRequest({ script: '/s.sh', login: 'a b' }).error, /user@host/)
  assert.deepEqual(launchRequest({ script: ' /archive/models/run.sh ', login: '' }, 'http://gpu-box.lan:8081/v1').body,
    { script: '/archive/models/run.sh', login: '', base_url: 'http://gpu-box.lan:8081/v1' })
  assert.deepEqual(serverForm({ script: '', default_script: '/archive/models/run-inkling.sh', login: '' }),
    { script: '/archive/models/run-inkling.sh', login: '' })
  assert.deepEqual(serverForm({ script: '/s.sh', login: 'a@h' }), { script: '/s.sh', login: 'a@h' })
})

test('script picker asks the folder of the typed script, else the default folder', () => {
  const q = new URLSearchParams(scriptsQuery({ script: '/srv/llm/a.sh', login: '' }, 'http://gpu-box.lan:8081/v1', '/archive/models'))
  assert.equal(q.get('folder'), '/srv/llm')
  assert.equal(q.get('base_url'), 'http://gpu-box.lan:8081/v1')
  assert.equal(new URLSearchParams(scriptsQuery({ script: '' }, '', '/archive/models')).get('folder'), '/archive/models')
})

test('server summary', () => {
  assert.match(serverSummary({ launched: null }), /No server/)
  const launched = { login: 'a@gpu-box.lan', pgid: 42, started_at: null }
  assert.match(serverSummary({ launched, running: true }), /^Running on a@gpu-box\.lan \(process group 42\)/)
  assert.match(serverSummary({ launched, running: false }), /no longer running/)
})
