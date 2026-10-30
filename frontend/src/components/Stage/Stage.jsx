import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import { api } from '../../api'
import { useApp, useAppStateRef } from '../../context/AppContext'
import { handleSessionEvent } from '../../eventHandlers'
import { findAnchorEventIndex } from '../../search'
import useWebSocket from '../../hooks/useWebSocket'
import useApi from '../../hooks/useApi'
import SplashScreen from './SplashScreen'
import MessageBubble from './MessageBubble'
import StatusMessage from './StatusMessage'
import ErrorMessage from './ErrorMessage'
import InlineIndicator from './InlineIndicator'
import TurnNotes from './TurnNotes'
import PromptActions from './PromptActions'
import { buildTurns, sameTurn, unansweredLastTurn } from '../../turnModel'
import ApprovalCard from './ApprovalCard'
import QuestionCard from './QuestionCard'

export default function Stage() {
  const { state, dispatch } = useApp()
  const { respondApproval, answerQuestion } = useApi()
  const stageRef = useRef(null)
  const stateRef = useAppStateRef()
  const windowFetchRef = useRef(false)

  useWebSocket(state.currentSessionId, dispatch, stateRef)

  useEffect(() => {
    if (state.scrollTarget) return
    if (stageRef.current) {
      stageRef.current.scrollTop = stageRef.current.scrollHeight
    }
  }, [state.stageItems, state.scrollTarget])

  // Search deep-linking: once the session's events are loaded, scroll to the
  // stage item nearest the targeted event index, paging in an older window of
  // the transcript first when the target predates the loaded events.
  useEffect(() => {
    const target = state.scrollTarget
    if (!target || state.isReplaying) return
    if (target.sessionId !== state.currentSessionId) return
    if (state.eventTotal === null) return

    if (target.eventIndex < state.eventWindowOffset) {
      if (state.isRunning || windowFetchRef.current) {
        if (state.isRunning) dispatch({ type: 'CLEAR_SCROLL_TARGET' })
        return
      }
      windowFetchRef.current = true
      const offset = Math.max(0, target.eventIndex - 40)
      api('GET', `/api/sessions/${encodeURIComponent(target.sessionId)}/events?offset=${offset}&limit=160`)
        .then((res) => {
          dispatch({ type: 'CLEAR_STAGE' })
          handleSessionEvent(
            {
              type: 'session_loaded',
              data: {
                meta: { status: 'idle' },
                events: res.events || [],
                event_offset: res.offset,
                event_total: res.total,
              },
            },
            dispatch,
            stateRef,
          )
          const shownThrough = res.offset + (res.events || []).length
          if (shownThrough < res.total) {
            dispatch({
              type: 'APPEND_STAGE_ITEM',
              payload: {
                type: 'status',
                text: `Showing an older part of the transcript (events ${res.offset + 1}–${shownThrough} of ${res.total}). Select the thread again to return to the latest.`,
              },
            })
          }
        })
        .catch(() => dispatch({ type: 'CLEAR_SCROLL_TARGET' }))
        .finally(() => { windowFetchRef.current = false })
      return
    }

    const anchor = findAnchorEventIndex(state.stageItems, target.eventIndex)
    if (anchor != null && stageRef.current) {
      const el = stageRef.current.querySelector(`[data-evt-index="${anchor}"]`)
      if (el) {
        el.scrollIntoView({ block: 'center' })
        el.classList.add('deeplink-flash')
        setTimeout(() => el.classList.remove('deeplink-flash'), 2200)
      }
    }
    dispatch({ type: 'CLEAR_SCROLL_TARGET' })
  }, [state.scrollTarget, state.isReplaying, state.currentSessionId, state.eventWindowOffset, state.eventTotal, state.stageItems, state.isRunning, dispatch])

  // Stable across renders so memoized ApprovalCards are not invalidated by
  // every streamed delta.
  const sessionId = state.currentSessionId
  const handleApproval = useCallback(
    (approvalId, approved) => respondApproval(sessionId, approvalId, approved),
    [respondApproval, sessionId],
  )
  const handleQuestion = useCallback(
    (questionId, answer) => answerQuestion(sessionId, questionId, answer),
    [answerQuestion, sessionId],
  )

  const renderItem = useCallback(function renderItem(item) {
    switch (item.type) {
      case 'user_message':
        return <MessageBubble role="user" text={item.text} images={item.images} attachments={item.attachments} steering={item.steering} />
      case 'assistant_message':
        return <MessageBubble role="assistant" markdown={item.markdown} />
      case 'assistant_stream':
        return <MessageBubble role="assistant" markdown={item.text} streaming />
      case 'status':
        return <StatusMessage text={item.text} />
      case 'error':
        return <ErrorMessage text={item.text} />
      case 'indicator':
        return <InlineIndicator text={item.text} />
      case 'approval':
        return (
          <ApprovalCard
            approvalId={item.approvalId}
            toolName={item.toolName}
            sourceAgent={item.sourceAgent}
            argsJson={item.argsJson}
            diffPreview={item.diffPreview}
            resolved={item.resolved}
            onRespond={handleApproval}
          />
        )
      case 'question':
        return (
          <QuestionCard
            questionId={item.questionId}
            question={item.question}
            options={item.options}
            allowFreeText={item.allowFreeText}
            answer={item.answer}
            answered={item.answered}
            submitting={item.submitting}
            submissionError={item.submissionError}
            onRespond={handleQuestion}
          />
        )
      default:
        return null
    }
  }, [handleApproval, handleQuestion])

  const turns = useMemo(() => buildTurns(state.stageItems), [state.stageItems])
  // The latest prompt, when it never got a reply: the one Retry / Delete apply to.
  const unansweredTurn = useMemo(() => unansweredLastTurn(turns, state.isRunning), [turns, state.isRunning])

  if (!sessionId) {
    return <SplashScreen />
  }

  return (
    <div
      ref={stageRef}
      className="@container flex-1 overflow-y-auto px-9 py-5"
    >
      <div className="max-w-[1080px] mx-auto flex flex-col gap-10">
        {turns.map((turn, i) => (
          <Turn
            key={turn.id}
            turn={turn}
            renderItem={renderItem}
            // Silent commands (/clear, /approve) also set isRunning but are not a turn
            // of work, so they get no live line.
            live={state.isRunning && !state.silentCommand && i === turns.length - 1}
            isLast={i === turns.length - 1}
            unanswered={turn === unansweredTurn}
          />
        ))}
      </div>
    </div>
  )
}

// One exchange: what was said on the left, what the agent did in the margin.
// Below ~48rem of stage width the notes drop underneath. Settled turns skip
// re-rendering while a later turn streams.
const Turn = memo(function Turn({ turn, renderItem, live, isLast, unanswered }) {
  const talk = [...(turn.user ? [turn.user] : []), ...turn.talk]
  return (
    <section className="grid gap-x-10 gap-y-3 @3xl:grid-cols-[minmax(0,1fr)_17rem]">
      <div className="flex min-w-0 flex-col">
        {talk.map((item, i) => {
          const node = renderItem(item)
          if (node === null) return null
          const isPrompt = item === turn.user
          return (
            <div key={item._id || i} className={`flex flex-col ${isPrompt ? 'group/prompt' : ''}`} data-evt-index={item.eventIndex ?? undefined}>
              {node}
              {isPrompt && !live && <PromptActions item={item} unanswered={unanswered} />}
            </div>
          )
        })}
      </div>
      <div className="min-w-0 @3xl:border-l @3xl:border-line @3xl:pl-5">
        {/* Pinned while this turn is on screen, so the plan, Stop and the live
            line stay in view next to a long reply. A notes list taller than the
            window scrolls inside itself; the notes still leave with their turn. */}
        <div className="@3xl:sticky @3xl:top-2 @3xl:max-h-[70vh] @3xl:overflow-y-auto">
          <TurnNotes turn={turn} live={live} isLast={isLast} />
        </div>
      </div>
    </section>
  )
}, (a, b) => a.live === b.live && a.isLast === b.isLast && a.unanswered === b.unanswered && a.renderItem === b.renderItem && sameTurn(a.turn, b.turn))
