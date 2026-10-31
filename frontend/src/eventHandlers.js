import { isSlashCommand } from './constants.js'
import { parseContextUsage } from './utils.js'
import { artifactPath } from './artifacts.js'
import { agentStatus, isAgentStateEvent } from './agents.js'

function previewLink(path, name = '') {
  const label = (name || String(path).split(/[\\/]/).pop()).replace(/[\[\]]/g, '')
  return `[View ${label}](${encodeURI(path).replace(/\(/g, '%28').replace(/\)/g, '%29')})`
}

export function toolDetail(_name, args) {
  if (!args) return ''
  if (args.file_path) return args.file_path
  if (args.directory && args.pattern) return `${args.directory} · ${args.pattern}`
  if (args.directory) return args.directory
  if (args.working_directory && args.command) return `${args.command} · ${args.working_directory}`
  if (args.command) return args.command
  if (args.url) return args.url
  if (args.keyword) return args.keyword
  if (args.query) return args.query
  if (args.path) return args.path
  if (args.file) return args.file
  return ''
}

export function isCodexProtocolStatus(text) {
  return [
    /^Starting Codex app-server run…$/,
    /^Resuming Codex thread…$/,
    /^Starting Codex thread…$/,
    /^Codex thread ready in \d+(?:\.\d+)?s\.$/,
    /^Starting Codex turn…$/,
    /^Codex turn accepted in \d+(?:\.\d+)?s\.$/,
    /^Waiting for Codex response…$/,
    /^Codex first response after \d+(?:\.\d+)?s\.$/,
    /^Interrupting Codex turn…$/,
    /^Codex run completed in \d+(?:\.\d+)?s\.$/,
    /^thread started$/,
    /^turn started$/,
  ].some(pattern => pattern.test(String(text || '')))
}

// Wraps dispatch so stage items appended while replaying a stored event carry
// the event's absolute index in the session log, used for search deep-linking.
function indexedDispatch(dispatch, eventIndex, historical = false) {
  return (action) => {
    if (historical && (action.type === 'APPEND_TOOL_CALL' || (action.type === 'APPEND_STAGE_ITEM' && !['user_message', 'assistant_message'].includes(action.payload.type)))) return
    if (action.type === 'UPDATE_TOOL_RESULT' && historical) return
    if (action.type === 'APPEND_STAGE_ITEM' || action.type === 'APPEND_TOOL_CALL') {
      dispatch({ ...action, payload: { ...action.payload, eventIndex, historical } })
    } else {
      dispatch(action)
    }
  }
}

export function handleSessionEvent(evt, dispatch, stateRef) {
  const { type, data } = evt

  switch (type) {
    case 'session_loaded': {
      // Replay is collected into a single BATCH action: a stored transcript of
      // N events costs one render instead of one render per event.
      const batch = []
      const collect = action => batch.push(action)
      collect({ type: 'SET_REPLAYING', payload: true })
      const offset = data.event_offset || 0
      collect({ type: 'SET_EVENT_WINDOW', payload: { offset, total: data.event_total ?? null } })
      const replayStateRef = { current: { ...stateRef.current, isReplaying: true } }
      const events = data.events || []
      const unresolvedQuestions = new Set()
      // A running session may reconnect mid-turn. Keep that turn interactive,
      // but all completed historical turns show exchanges only.
      let activeStart = events.length
      if (data.meta?.status === 'running') {
        for (let i = events.length - 1; i >= 0; i--) {
          if (events[i].type === 'run_finished') { activeStart = i + 1; break }
          if (events[i].type === 'user_message' && !events[i].data?.steering) { activeStart = i; break }
        }
        if (activeStart === events.length && !events.some(e => e.type === 'run_finished')) activeStart = 0
      }
      for (let i = 0; i < events.length; i++) {
        const e = events[i]
        if (e.type === 'question_required') unresolvedQuestions.add(e.data?.question_id)
        if (e.type === 'question_resolved') unresolvedQuestions.delete(e.data?.question_id)
        collect({ type: 'INCREMENT_REPLAYED_EVENTS' })
        if (i < activeStart && e.type === 'agent_event') continue
        if (e.type !== 'run_finished' || data.meta?.status !== 'running') {
          handleSessionEvent(e, indexedDispatch(collect, offset + i, i < activeStart), replayStateRef)
        }
      }
      if (data.meta?.id) {
        collect({ type: 'SET_QUEUE', payload: data.meta.message_queue || [] })
        collect({ type: 'SET_STEERING', payload: data.meta.steering_queue || [] })
      }
      collect({ type: 'SET_REPLAYING', payload: false })
      if (data.meta?.status !== 'running') {
        for (const questionId of unresolvedQuestions) {
          if (questionId) {
            collect({
              type: 'RESOLVE_QUESTION',
              payload: { questionId, answer: '', answered: false },
            })
          }
        }
        collect({ type: 'SET_REPLAY_IDLE' })
      }
      dispatch({ type: 'BATCH', payload: batch })
      break
    }

    case 'user_message': {
      if (stateRef.current.silentCommand || isSlashCommand(data.text)) break
      // A new prompt starts a fresh set of agents; the last turn's are done with.
      if (!data.steering && Object.keys(stateRef.current.agentsById || {}).length) dispatch({ type: 'CLEAR_AGENTS' })
      const isLiveRunStart = !stateRef.current.isReplaying && !stateRef.current.isRunning
      if (isLiveRunStart) {
        dispatch({ type: 'SET_RUNNING' })
      }
      dispatch({
        type: 'APPEND_STAGE_ITEM',
        payload: {
          type: 'user_message',
          steering: !!data.steering,
          text: data.text,
          images: data.images || [],
          attachments: data.attachments || data.images || [],
        },
      })
      if (!data.steering && (stateRef.current.isRunning || isLiveRunStart)) {
        if (stateRef.current.currentProvider === 'codex-app-server') {
          dispatch({ type: 'SHOW_CODEX_RUNNING' })
        } else {
          dispatch({ type: 'SET_ITERATION', payload: { n: 0, max: data.max || null } })
        }
      }
      break
    }

    case 'thinking':
      // Persisted counterpart of any streamed trace; replaces it so replays
      // and live runs end up with the same stage.
      dispatch({ type: 'CLEAR_THINKING_STREAM' })
      dispatch({ type: 'APPEND_THINKING', payload: data.markdown || '' })
      break

    case 'thinking_delta':
      // Live-only streamed reasoning; never part of stored replays.
      if (stateRef.current.isReplaying) break
      dispatch({ type: 'APPEND_THINKING_DELTA', payload: data.text || '' })
      break

    case 'queue_updated':
      dispatch({ type: 'SET_STEERING', payload: data.steering_items || [] })
      dispatch({ type: 'SET_QUEUE', payload: data.items || [] })
      break

    case 'plan_update':
      dispatch({ type: 'SET_PLAN', payload: data.items || [] })
      break

    case 'turn_note':
      // A model-written margin note. Stored with the transcript, so replays
      // show the same notes as the live run.
      if (data.text) dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'turn_note', text: data.text } })
      break

    case 'assistant_delta':
      // Live-only streamed text; never part of stored replays.
      if (stateRef.current.isReplaying) break
      dispatch({ type: 'CLEAR_ITERATION' })
      dispatch({ type: 'APPEND_ASSISTANT_DELTA', payload: data.text || '' })
      break

    case 'assistant_message':
      dispatch({ type: 'CLEAR_ASSISTANT_STREAM' })
      dispatch({ type: 'FINALIZE_WORK_GROUP' })
      dispatch({ type: 'HIDE_CODEX_RUNNING' })
      dispatch({ type: 'CLEAR_ITERATION' })
      // A final answer that embeds an image already shown when the tool wrote
      // it replaces that early preview instead of repeating it.
      dispatch({ type: 'DROP_SHOWN_GENERATED_IMAGES', payload: data.markdown || '' })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'assistant_message', markdown: data.markdown } })
      break

    case 'tool_call':
      // Streamed commentary preceding tool calls is not persisted; drop it so
      // the live view matches the stored transcript.
      dispatch({ type: 'CLEAR_ASSISTANT_STREAM' })
      // Recorded in every verbosity mode: the margin notes are built from these.
      // With verbose off the backend sends no arguments and only the first
      // line of each result, so the raw details are marked as hidden.
      dispatch({
        type: 'APPEND_TOOL_CALL',
        payload: {
          callId: data.call_id,
          name: data.name,
          args: data.args,
          detail: data.args ? toolDetail(data.name, data.args) : '',
          statusLine: data.status_line || data.name,
          status: 'running',
          resultPreview: null,
          detailsHidden: !data.verbose,
          startedAt: evt.created_at ? Date.parse(evt.created_at) : Date.now(),
        },
      })
      break

    case 'tool_result':
      dispatch({
        type: 'UPDATE_TOOL_RESULT',
        payload: {
          callId: data.call_id,
          name: data.name,
          preview: data.preview || '',
          ok: data.ok !== false,
          durationMs: data.duration_ms,
        },
      })
      break

    case 'approval_required':
      dispatch({ type: 'CLEAR_ITERATION' })
      dispatch({
        type: 'APPEND_STAGE_ITEM',
        payload: {
          type: 'approval',
          approvalId: data.approval_id,
          toolName: data.tool_name,
          sourceAgent: data.source_agent,
          argsJson: data.args_json,
          diffPreview: data.diff_preview,
          resolved: null,
        },
      })
      break

    case 'approval_resolved':
      dispatch({ type: 'RESOLVE_APPROVAL', payload: { approvalId: data.approval_id, approved: data.approved } })
      break

    case 'question_required':
      dispatch({ type: 'CLEAR_ASSISTANT_STREAM' })
      dispatch({ type: 'CLEAR_THINKING_STREAM' })
      dispatch({ type: 'CLEAR_ITERATION' })
      dispatch({
        type: 'APPEND_STAGE_ITEM',
        payload: {
          type: 'question',
          questionId: data.question_id,
          question: data.question,
          options: data.options || [],
          allowFreeText: data.allow_free_text !== false,
          answer: null,
          answered: null,
          submitting: false,
          submissionError: null,
        },
      })
      break

    case 'question_resolved':
      dispatch({
        type: 'RESOLVE_QUESTION',
        payload: { questionId: data.question_id, answer: data.answer || '', answered: !!data.answered },
      })
      break

    case 'api_metrics': {
      // Compaction is billed/recorded in run totals, but its hidden synthesis
      // must not replace the visible answer's throughput. estimated_input is
      // optional local category telemetry, separate from provider usage.
      // The composer shows speed only; token and cache counts live in the
      // Usage panel.
      if (data.purpose === 'history_compaction') break
      const speed = data.tokens_per_sec || data.tps
      if (speed) dispatch({ type: 'SET_THROUGHPUT', payload: `${speed} tok/s` })
      if (data.usage_str) dispatch({ type: 'SET_CONTEXT', payload: parseContextUsage(data.usage_str) })
      break
    }

    case 'context_usage':
      dispatch({ type: 'SET_CONTEXT', payload: parseContextUsage(data.usage_str) })
      break

    case 'status':
      if (
        !stateRef.current.verbose
        && isCodexProtocolStatus(data.text)
      ) break
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: stateRef.current.silentCommand ? 'indicator' : 'status', text: data.text } })
      if (data.approval_mode) dispatch({ type: 'SET_APPROVAL_MODE', payload: data.approval_mode })
      if (data.verbose !== undefined) dispatch({ type: 'SET_VERBOSE', payload: data.verbose })
      break

    case 'error':
      dispatch({ type: 'CLEAR_ASSISTANT_STREAM' })
      dispatch({ type: 'CLEAR_THINKING_STREAM' })
      dispatch({ type: 'CLEAR_ITERATION' })
      dispatch({ type: 'HIDE_CODEX_RUNNING' })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: data.text } })
      break

    case 'iteration':
      dispatch({ type: 'SET_ITERATION', payload: { n: data.n, max: data.max } })
      break

    case 'compaction':
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'status', text: `Compacting: ${data.before} → ${data.after} tokens` } })
      break

    case 'file_change':
      dispatch({
        type: 'APPEND_FILE_CHANGE',
        payload: { path: data.path, action: data.action, tool: data.tool, timestamp: evt.created_at || new Date().toISOString() },
      })
      if (data.action === 'created' && artifactPath(data.path)) {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'assistant_message', markdown: previewLink(data.path) } })
      }
      break

    case 'generated_artifact': {
      const name = data.name || String(data.path || '').split('/').pop() || 'generated image'
      if (data.media_type?.startsWith('text/')) {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'assistant_message', markdown: previewLink(data.path, name) } })
        dispatch({ type: 'APPEND_FILE_CHANGE', payload: { path: data.path, action: 'generated', tool: 'shell_run', timestamp: evt.created_at } })
        break
      }
      const version = data.version || evt.created_at || Date.now()
      const separator = String(data.path || '').includes('?') ? '&' : '?'
      const path = `${data.path}${separator}myharness_v=${encodeURIComponent(version)}`
      dispatch({
        type: 'APPEND_STAGE_ITEM',
        payload: { type: 'assistant_message', markdown: `![${name}](${path})`, generatedPath: data.path || '' },
      })
      break
    }

    case 'codex_command':
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'codex_command', command: data.command, status: data.status, tool: data.tool, label: data.label, callId: data.call_id } })
      break

    case 'codex_file_change':
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'codex_file_change', path: data.path, status: data.status } })
      // Older stored Codex events doubled as the Changes-panel record. New
      // runs emit a dedicated file_change event so the transcript card can
      // remain verbose-only without losing the change record.
      if (data.records_change !== false) {
        dispatch({
          type: 'APPEND_FILE_CHANGE',
          payload: { path: data.path, action: data.status || 'modified', tool: 'codex', timestamp: evt.created_at || new Date().toISOString() },
        })
      }
      break

    case 'codex_item':
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'codex_item', itemType: data.item_type, raw: data.raw } })
      break

    case 'agent_event': {
      // State changes feed the Agents list in the margin notes, in every
      // verbosity mode. Progress lines (what a child is reading or running) stay
      // verbose-only status items.
      if (isAgentStateEvent(data)) {
        const status = agentStatus(data)
        dispatch({ type: 'UPSERT_AGENT', payload: status === undefined ? { ...data } : { ...data, status } })
      }
      const verbose = stateRef.current.verbose
      if (!verbose) break
      const agent = data.agent_path || data.agent_id || 'agent'
      const detail = String(data.error || data.result || data.text || data.task || data.status || '')
      const text = `[${agent}] ${data.action || 'updated'}${detail ? `: ${detail}` : ''}`
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: data.action === 'error' || data.action === 'failed' ? 'error' : 'status', text } })
      break
    }

    case 'provider_warning':
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'status', text: data.message } })
      break

    case 'provider_switch':
      // The authoritative current provider/workspace state already arrives
      // with the freshly-fetched session meta on selection. Replaying these
      // historical mutations would step currentMeta through stale
      // point-in-time snapshots. Keep the transcript note, skip the mutation.
      if (!stateRef.current.isReplaying) {
        dispatch({ type: 'SET_PROVIDER', payload: data.provider || 'native' })
      }
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: stateRef.current.silentCommand ? 'indicator' : 'status', text: data.text } })
      break

    case 'workspace_changed':
      if (stateRef.current.isReplaying) break
      dispatch({
        type: 'SET_WORKSPACE_ROOT',
        payload: {
          root: data.current || '',
          workingDirectory: data.working_directory || '',
        },
      })
      break

    case 'run_finished':
      dispatch({ type: 'CLEAR_ASSISTANT_STREAM' })
      dispatch({ type: 'CLEAR_THINKING_STREAM' })
      dispatch({ type: 'FINALIZE_WORK_GROUP' })
      if (Object.keys(stateRef.current.agentsById || {}).length) dispatch({ type: 'FINALIZE_AGENTS' })
      // Slash commands also finish a run; only a prompt's own run says how it ended.
      if (data.reason && data.reason !== 'command') dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'run_end', reason: data.reason } })
      if (data.reason === 'max iterations reached') {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: 'Max iterations were reached and no response was generated. Increase the limit with /maxiters n and try again.' } })
      } else if (data.reason === 'interrupted') {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'status', text: 'Run interrupted. Context rolled back to the last completed turn.' } })
      } else if (data.reason === 'api timeout') {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: 'Run stopped because the model API timed out. Try again, or reduce the request/context size.' } })
      }
      dispatch({ type: 'SET_IDLE' })
      dispatch({ type: 'SET_SILENT_COMMAND', payload: false })
      break
  }
}
