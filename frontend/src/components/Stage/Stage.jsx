import { useCallback, useEffect, useRef } from 'react'
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
import WorkGroup from './WorkGroup'
import ApprovalCard from './ApprovalCard'
import CodexCommand from './CodexCommand'
import CodexFileChange from './CodexFileChange'
import ThinkingBlock from './ThinkingBlock'

export default function Stage() {
  const { state, dispatch } = useApp()
  const { respondApproval } = useApi()
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

  if (!sessionId) {
    return <SplashScreen />
  }

  function renderItem(item) {
    switch (item.type) {
      case 'user_message':
        return <MessageBubble role="user" text={item.text} images={item.images} attachments={item.attachments} />
      case 'thinking':
        return <ThinkingBlock markdown={item.markdown} />
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
      case 'work_group':
        return (
          <WorkGroup
            tools={item.tools}
            startTime={item.startTime}
            finalized={item.finalized}
          />
        )
      case 'approval':
        return (
          <ApprovalCard
            approvalId={item.approvalId}
            toolName={item.toolName}
            argsJson={item.argsJson}
            diffPreview={item.diffPreview}
            resolved={item.resolved}
            onRespond={handleApproval}
          />
        )
      case 'codex_command':
        return <CodexCommand command={item.command} status={item.status} />
      case 'codex_file_change':
        return <CodexFileChange path={item.path} status={item.status} />
      default:
        return null
    }
  }

  return (
    <div
      ref={stageRef}
      className="flex-1 overflow-y-auto px-7 py-5"
    >
      <div className="max-w-[820px] mx-auto flex flex-col">
        {state.stageItems.map((item, i) => {
          const key = item._id || i
          const node = renderItem(item)
          if (node === null) return null
          return (
            <div key={key} className="flex flex-col" data-evt-index={item.eventIndex ?? undefined}>
              {node}
            </div>
          )
        })}
      </div>
    </div>
  )
}
