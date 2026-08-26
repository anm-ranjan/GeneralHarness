export async function submitQuestion(dispatch, post, sessionId, questionId, answer) {
  dispatch({ type: 'SUBMIT_QUESTION', payload: { questionId } })
  try {
    await post('POST', `/api/sessions/${encodeURIComponent(sessionId)}/question`, {
      question_id: questionId,
      answer,
    })
    dispatch({ type: 'RESOLVE_QUESTION', payload: { questionId, answer, answered: true } })
    return true
  } catch (err) {
    dispatch({
      type: 'QUESTION_SUBMISSION_FAILED',
      payload: { questionId, error: err.detail || err.message || 'Could not submit the answer. Please try again.' },
    })
    return false
  }
}
