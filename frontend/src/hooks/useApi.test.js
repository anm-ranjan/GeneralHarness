import assert from 'node:assert/strict'
import test from 'node:test'

import { submitQuestion } from './questionSubmission.js'

test('question submission resolves only after the server accepts it', async () => {
  const actions = []
  let accept
  const post = () => new Promise(resolve => { accept = resolve })
  const pending = submitQuestion(action => actions.push(action), post, 'ses 1', 'qst_1', 'strict')

  assert.deepEqual(actions.map(action => action.type), ['SUBMIT_QUESTION'])
  accept({ status: 'answered' })
  assert.equal(await pending, true)
  assert.deepEqual(actions.map(action => action.type), ['SUBMIT_QUESTION', 'RESOLVE_QUESTION'])
  assert.deepEqual(actions[1].payload, {
    questionId: 'qst_1', answer: 'strict', answered: true,
  })
})

test('failed question submission exposes an inline retryable error', async () => {
  const actions = []
  const post = async () => { throw { detail: 'Question expired' } }

  assert.equal(
    await submitQuestion(action => actions.push(action), post, 'ses_1', 'qst_1', 'strict'),
    false,
  )
  assert.deepEqual(actions.map(action => action.type), [
    'SUBMIT_QUESTION', 'QUESTION_SUBMISSION_FAILED',
  ])
  assert.deepEqual(actions[1].payload, {
    questionId: 'qst_1', error: 'Question expired',
  })
})
