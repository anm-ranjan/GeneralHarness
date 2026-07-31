// Pure state layer for the app store. Kept free of JSX so it can be unit
// tested directly with `node --test`.
import { effectiveWorkspaceRoot } from '../sessionWorkspace.js'
import { applyRunState, snapshotRunStates } from '../runStates.js'

export const initialState = {
  currentSessionId: null,
  currentMeta: null,
  currentProvider: 'native',
  currentWorkspaceRoot: '',

  isRunning: false,
  isCancelling: false,

  codexAppServerEnabled: false,
  claudeAgentEnabled: false,
  nativeEnabled: false,
  defaultProvider: 'native',
  appName: 'MyHarness',
  splashAscii: '',
  model: '',
  approvalMode: '',
  verbose: false,
  serverOnline: false,
  desktopEnabled: false,
  electronOnly: false,
  desktopBackendUrl: '',

  projects: [],
  sessionsById: {},
  projectRoots: {},
  projectNames: {},
  taskNames: {},

  stageItems: [],
  queuedMessages: [],
  plan: [],

  contextPercent: 0,
  contextLabel: '',
  contextTokens: '',
  throughput: '',

  showDirectoryPicker: false,
  showProviderPicker: null,
  showSearch: false,
  showSettings: false,
  gitWritesEnabled: false,
  audioEnabled: false,
  audioMaxUploadMb: 500,

  silentCommand: false,
  lastActivityTime: Date.now(),

  showCodexRunning: false,
  iterationN: null,
  iterationMax: null,

  workspacePanelOpen: false,
  workspacePanelTab: 'changes',
  workspaceEditorFile: null,
  workspaceEditorExpanded: false,
  touchedFiles: [],
  diffViewerFile: null,
  confirmDialog: null,
  isReplaying: false,
  replayedEventCount: 0,
  wsConnected: false,
  wsReconnects: 0,

  scrollTarget: null,
  eventWindowOffset: 0,
  eventTotal: null,

  // session_id -> 'running' | 'waiting_approval' for non-idle sessions,
  // fed by the application-level /api/events stream.
  runStates: {},

  // The machines this UI can switch between. Empty means single-machine mode
  // and the host switcher stays hidden. Each host owns its own projects,
  // tasks, and sessions; only one is ever loaded at a time.
  fleetHosts: [],
  activeHostId: '',
  // host_id -> { online, running, waitingApproval } from the fleet status poll.
  fleetStatuses: {},
  // True between choosing a host and that host's session tree arriving.
  hostSwitching: false,
}

// Returns the same state object when every patched field already matches, so
// repeated identical updates (the 5s health poll) cost zero re-renders.
function patched(state, patch) {
  for (const key in patch) {
    if (state[key] !== patch[key]) return { ...state, ...patch }
  }
  return state
}

function buildNameMaps(projects) {
  const projectRoots = {}
  const projectNames = {}
  const taskNames = {}
  for (const p of projects) {
    projectRoots[p.id] = p.root
    projectNames[p.id] = p.name
    for (const t of p.tasks || []) {
      taskNames[t.id] = t.name
    }
  }
  return { projectRoots, projectNames, taskNames }
}

function appendStageItem(items, item) {
  return [...items, { ...item, _id: item._id || `si_${Date.now()}_${Math.random().toString(36).slice(2, 6)}` }]
}

function finalizeLastWorkGroup(items) {
  const last = items[items.length - 1]
  if (last && last.type === 'work_group' && !last.finalized) {
    const updated = [...items]
    updated[updated.length - 1] = {
      ...last,
      finalized: true,
      tools: last.tools.map(tool =>
        tool.status === 'running' ? { ...tool, status: 'stopped' } : tool
      ),
    }
    return updated
  }
  return items
}

function finalizeAllWorkGroups(items) {
  return items.map(item => {
    if (item.type !== 'work_group' || item.finalized) return item
    return {
      ...item,
      finalized: true,
      tools: item.tools.map(tool =>
        tool.status === 'running' ? { ...tool, status: 'stopped' } : tool
      ),
    }
  })
}

function queueItemView(item) {
  const attachmentCount = item.attachment_count ?? item.attachmentCount ?? (item.attachments || item.images || []).length ?? 0
  return {
    id: item.id,
    text: item.text || '',
    imageCount: item.image_count ?? item.imageCount ?? (item.images || []).length ?? 0,
    attachmentCount,
    createdAt: item.created_at || item.createdAt || '',
  }
}

export function reducer(state, action) {
  switch (action.type) {
    case 'SET_HEALTH': {
      return patched(state, {
        serverOnline: true,
        codexAppServerEnabled: !!(action.payload.codex_app_server_enabled ?? action.payload.codex_enabled),
        claudeAgentEnabled: !!action.payload.claude_agent_enabled,
        nativeEnabled: !!action.payload.native_enabled,
        defaultProvider: action.payload.default_provider || 'native',
        appName: action.payload.app_name || 'MyHarness',
        splashAscii: action.payload.splash_ascii || '',
        model: action.payload.model || '',
        // Per-session run settings take precedence over the process defaults
        // reported by the health poll.
        approvalMode: state.currentMeta?.run_settings?.approval_mode
          || action.payload.approval_mode
          || '',
        verbose: typeof state.currentMeta?.run_settings?.verbose_tools === 'boolean'
          ? state.currentMeta.run_settings.verbose_tools
          : !!action.payload.verbose,
        desktopEnabled: !!action.payload.desktop_enabled,
        electronOnly: !!action.payload.electron_only,
        desktopBackendUrl: action.payload.desktop_backend_url || '',
        gitWritesEnabled: !!action.payload.git_writes_enabled,
        audioEnabled: !!action.payload.audio?.enabled,
        audioMaxUploadMb: action.payload.audio?.max_upload_mb || state.audioMaxUploadMb,
      })
    }

    case 'SET_SERVER_ONLINE':
      return patched(state, { serverOnline: action.payload })

    case 'SET_FLEET':
      return patched(state, {
        fleetHosts: action.payload.hosts,
        activeHostId: action.payload.activeHostId,
      })

    case 'SET_HOST_STATUS': {
      const { hostId, status } = action.payload
      const previous = state.fleetStatuses[hostId]
      if (
        previous
        && previous.online === status.online
        && previous.running === status.running
        && previous.waitingApproval === status.waitingApproval
        && previous.reportedId === status.reportedId
      ) {
        return state
      }
      return {
        ...state,
        fleetStatuses: { ...state.fleetStatuses, [hostId]: status },
      }
    }

    // Switching machines replaces the entire workspace rather than merging
    // anything, so every host-owned slice resets to its initial value. Keeping
    // a stale field here is how one host's data ends up rendered under the
    // other's label, so this deliberately rebuilds from initialState instead of
    // clearing fields one by one.
    case 'SWITCH_HOST':
      return {
        ...initialState,
        // The registry came from the host that served the page and describes
        // the fleet, not the machine being viewed. Status polling likewise
        // keeps running for every host across the switch.
        fleetHosts: state.fleetHosts,
        fleetStatuses: state.fleetStatuses,
        activeHostId: action.payload.hostId,
        hostSwitching: true,
        // Panel layout is a user preference rather than host data.
        workspacePanelOpen: state.workspacePanelOpen,
        workspacePanelTab: state.workspacePanelTab,
        lastActivityTime: Date.now(),
      }

    // The target host died between the status poll and the switch. The
    // workspace stays empty and the health poll reports it offline; this just
    // stops the switcher claiming it is still working on it.
    case 'HOST_SWITCH_FAILED':
      return patched(state, { hostSwitching: false })

    case 'SET_TREE': {
      const { projects, sessions } = action.payload
      const maps = buildNameMaps(projects)
      const currentMeta = state.currentSessionId && sessions[state.currentSessionId]
        ? sessions[state.currentSessionId]
        : state.currentMeta
      const currentProjectRoot = currentMeta ? maps.projectRoots[currentMeta.project_id] || '' : ''
      return {
        ...state,
        projects,
        sessionsById: sessions,
        currentMeta,
        currentWorkspaceRoot: currentMeta
          ? effectiveWorkspaceRoot(currentMeta, currentProjectRoot)
          : state.currentWorkspaceRoot,
        // The tree is the last thing a switch waits on: once it lands, the
        // new host's workspace is what the user is looking at.
        hostSwitching: false,
        ...maps,
      }
    }

    case 'SELECT_SESSION': {
      const { meta, workspaceRoot } = action.payload
      return {
        ...state,
        currentSessionId: meta.id,
        currentMeta: meta,
        currentProvider: meta.provider || 'native',
        currentWorkspaceRoot: workspaceRoot || '',
        approvalMode: meta.run_settings?.approval_mode || state.approvalMode,
        verbose: typeof meta.run_settings?.verbose_tools === 'boolean'
          ? meta.run_settings.verbose_tools
          : state.verbose,
        isRunning: meta.status === 'running',
        isCancelling: false,
        stageItems: [],
        queuedMessages: (meta.message_queue || []).map(queueItemView),
        plan: [],
        touchedFiles: [],
        diffViewerFile: null,
        // The editor points at a path in the previous session's workspace.
        workspaceEditorFile: null,
        workspaceEditorExpanded: false,
        iterationN: null,
        iterationMax: null,
        contextPercent: 0,
        contextLabel: '',
        contextTokens: '',
        throughput: '',
        eventWindowOffset: 0,
        eventTotal: null,
        scrollTarget: state.scrollTarget && state.scrollTarget.sessionId === meta.id
          ? state.scrollTarget
          : null,
      }
    }

    case 'CLEAR_SESSION':
      return {
        ...state,
        currentSessionId: null,
        currentMeta: null,
        stageItems: [],
        queuedMessages: [],
        plan: [],
        isRunning: false,
        isCancelling: false,
        iterationN: null,
        iterationMax: null,
      }

    case 'SET_RUNNING':
      return { ...state, isRunning: true, isCancelling: false, lastActivityTime: Date.now(), iterationN: null, iterationMax: null }

    case 'SET_CANCELLING':
      return { ...state, isCancelling: true }

    case 'SET_IDLE':
      return { ...state, isRunning: false, isCancelling: false, lastActivityTime: Date.now(), showCodexRunning: false, iterationN: null, iterationMax: null }

    case 'SET_REPLAY_IDLE':
      return {
        ...state,
        isRunning: false,
        isCancelling: false,
        showCodexRunning: false,
        iterationN: null,
        iterationMax: null,
        stageItems: finalizeAllWorkGroups(state.stageItems),
      }

    case 'SET_REPLAYING':
      return {
        ...state,
        isReplaying: action.payload,
        replayedEventCount: action.payload ? 0 : state.replayedEventCount,
      }

    case 'INCREMENT_REPLAYED_EVENTS':
      return { ...state, replayedEventCount: state.replayedEventCount + (action.payload || 1) }

    case 'SET_QUEUE':
      return { ...state, queuedMessages: (action.payload || []).map(queueItemView) }

    case 'SET_PLAN':
      return { ...state, plan: action.payload || [] }

    case 'SET_WS_CONNECTED':
      return patched(state, { wsConnected: action.payload })

    // Folds a list of actions into one state transition. Session replay uses
    // this so loading a transcript renders once instead of once per event.
    case 'BATCH':
      return action.payload.reduce(reducer, state)

    case 'SET_EVENT_WINDOW':
      return {
        ...state,
        eventWindowOffset: action.payload.offset || 0,
        eventTotal: action.payload.total ?? state.eventTotal,
      }

    case 'SET_SCROLL_TARGET':
      return { ...state, scrollTarget: action.payload }

    case 'CLEAR_SCROLL_TARGET':
      return { ...state, scrollTarget: null }

    case 'SET_RUN_STATES':
      return { ...state, runStates: snapshotRunStates(action.payload) }

    case 'SET_SESSION_RUN_STATE': {
      const { sessionId, state: runState } = action.payload
      const sessionsById = state.sessionsById[sessionId]
        ? {
            ...state.sessionsById,
            [sessionId]: {
              ...state.sessionsById[sessionId],
              status: runState === 'idle' ? 'idle' : 'running',
            },
          }
        : state.sessionsById
      return {
        ...state,
        runStates: applyRunState(state.runStates, sessionId, runState),
        sessionsById,
      }
    }

    case 'INCREMENT_WS_RECONNECTS':
      return { ...state, wsReconnects: state.wsReconnects + 1 }

    case 'APPEND_STAGE_ITEM':
      return { ...state, stageItems: appendStageItem(state.stageItems, action.payload) }

    case 'APPEND_ASSISTANT_DELTA': {
      const items = state.stageItems
      const last = items[items.length - 1]
      if (last && last.type === 'assistant_stream') {
        const updated = [...items]
        updated[updated.length - 1] = { ...last, text: last.text + action.payload }
        return { ...state, stageItems: updated }
      }
      return { ...state, stageItems: appendStageItem(items, { type: 'assistant_stream', text: action.payload }) }
    }

    case 'CLEAR_ASSISTANT_STREAM': {
      const items = state.stageItems
      const last = items[items.length - 1]
      if (!last || last.type !== 'assistant_stream') return state
      return { ...state, stageItems: items.slice(0, -1) }
    }

    case 'APPEND_TOOL_CALL': {
      const items = state.stageItems
      const last = items[items.length - 1]
      const toolItem = action.payload
      if (last && last.type === 'work_group' && !last.finalized) {
        const updated = [...items]
        updated[updated.length - 1] = {
          ...last,
          tools: [...last.tools, toolItem],
        }
        return { ...state, stageItems: updated }
      }
      return {
        ...state,
        stageItems: appendStageItem(items, {
          type: 'work_group',
          startTime: toolItem.startedAt || Date.now(),
          finalized: false,
          eventIndex: toolItem.eventIndex,
          tools: [toolItem],
        }),
      }
    }

    case 'UPDATE_TOOL_RESULT': {
      const { callId, name, preview, ok, durationMs } = action.payload
      const items = [...state.stageItems]
      for (let i = items.length - 1; i >= 0; i--) {
        if (items[i].type === 'work_group') {
          const tools = [...items[i].tools]
          for (let j = tools.length - 1; j >= 0; j--) {
            const matches = callId ? tools[j].callId === callId : tools[j].name === name
            if (matches && tools[j].status === 'running') {
              tools[j] = {
                ...tools[j],
                status: ok ? 'done' : 'failed',
                resultPreview: preview,
                durationMs,
              }
              items[i] = { ...items[i], tools }
              return { ...state, stageItems: items }
            }
          }
        }
      }
      return state
    }

    case 'FINALIZE_WORK_GROUP':
      return { ...state, stageItems: finalizeLastWorkGroup(state.stageItems) }

    case 'CLEAR_STAGE':
      return { ...state, stageItems: [] }

    case 'SET_CONTEXT': {
      const { percent, label, tokens } = action.payload
      return {
        ...state,
        contextPercent: percent ?? state.contextPercent,
        contextLabel: label ?? state.contextLabel,
        contextTokens: tokens ?? state.contextTokens,
      }
    }

    case 'SET_THROUGHPUT':
      return { ...state, throughput: action.payload }

    case 'SET_APPROVAL_MODE':
      return {
        ...state,
        approvalMode: action.payload,
        currentMeta: state.currentMeta
          ? {
              ...state.currentMeta,
              run_settings: { ...state.currentMeta.run_settings, approval_mode: action.payload },
            }
          : state.currentMeta,
      }

    case 'SET_VERBOSE':
      return {
        ...state,
        verbose: action.payload,
        currentMeta: state.currentMeta
          ? {
              ...state.currentMeta,
              run_settings: { ...state.currentMeta.run_settings, verbose_tools: action.payload },
            }
          : state.currentMeta,
      }

    case 'SET_RUN_SETTINGS': {
      if (!state.currentMeta) return state
      const runSettings = { ...action.payload }
      return {
        ...state,
        currentMeta: { ...state.currentMeta, run_settings: runSettings },
        sessionsById: {
          ...state.sessionsById,
          [state.currentMeta.id]: {
            ...(state.sessionsById[state.currentMeta.id] || state.currentMeta),
            run_settings: runSettings,
          },
        },
      }
    }

    case 'SET_PROVIDER': {
      const provider = action.payload
      return {
        ...state,
        currentProvider: provider,
        currentMeta: state.currentMeta
          ? { ...state.currentMeta, provider }
          : state.currentMeta,
      }
    }

    case 'OPEN_DIR_PICKER':
      return { ...state, showDirectoryPicker: true }

    case 'CLOSE_DIR_PICKER':
      return { ...state, showDirectoryPicker: false }

    case 'OPEN_PROVIDER_PICKER':
      return { ...state, showProviderPicker: action.payload }

    case 'CLOSE_PROVIDER_PICKER':
      return { ...state, showProviderPicker: null }

    case 'OPEN_SEARCH':
      return { ...state, showSearch: true }

    case 'CLOSE_SEARCH':
      return { ...state, showSearch: false }

    case 'OPEN_SETTINGS':
      return { ...state, showSettings: true }

    case 'CLOSE_SETTINGS':
      return { ...state, showSettings: false }

    case 'SET_SILENT_COMMAND':
      return { ...state, silentCommand: action.payload }

    case 'SHOW_CODEX_RUNNING':
      return { ...state, showCodexRunning: true }

    case 'HIDE_CODEX_RUNNING':
      return { ...state, showCodexRunning: false }

    case 'SET_ITERATION':
      return { ...state, iterationN: action.payload.n, iterationMax: action.payload.max }

    case 'CLEAR_ITERATION':
      return { ...state, iterationN: null, iterationMax: null }

    case 'RESOLVE_APPROVAL': {
      const { approvalId, approved } = action.payload
      const items = state.stageItems.map(item =>
        item.type === 'approval' && item.approvalId === approvalId
          ? { ...item, resolved: approved ? 'approved' : 'denied' }
          : item
      )
      return { ...state, stageItems: items }
    }

    case 'UPDATE_SESSION_META':
      return {
        ...state,
        currentMeta: state.currentMeta
          ? { ...state.currentMeta, ...action.payload }
          : state.currentMeta,
      }

    case 'SET_WORKSPACE_ROOT': {
      const workingDirectory = action.payload.workingDirectory ?? action.payload.root ?? ''
      const metaPatch = { working_directory: workingDirectory }
      return {
        ...state,
        currentWorkspaceRoot: action.payload.root || '',
        currentMeta: state.currentMeta
          ? { ...state.currentMeta, ...metaPatch }
          : state.currentMeta,
        sessionsById: state.currentSessionId && state.sessionsById[state.currentSessionId]
          ? {
              ...state.sessionsById,
              [state.currentSessionId]: {
                ...state.sessionsById[state.currentSessionId],
                ...metaPatch,
              },
            }
          : state.sessionsById,
      }
    }

    case 'TOGGLE_WORKSPACE_PANEL':
      return { ...state, workspacePanelOpen: !state.workspacePanelOpen }

    case 'SET_WORKSPACE_TAB':
      return { ...state, workspacePanelTab: action.payload }

    case 'OPEN_WORKSPACE_EDITOR':
      return { ...state, workspaceEditorFile: action.payload }

    case 'CLOSE_WORKSPACE_EDITOR':
      return { ...state, workspaceEditorFile: null, workspaceEditorExpanded: false }

    case 'TOGGLE_WORKSPACE_EDITOR_EXPANDED':
      return { ...state, workspaceEditorExpanded: !state.workspaceEditorExpanded }

    case 'APPEND_FILE_CHANGE': {
      const fc = action.payload
      const exists = state.touchedFiles.findIndex(f => f.path === fc.path)
      if (exists >= 0) {
        const updated = [...state.touchedFiles]
        updated[exists] = { ...updated[exists], action: fc.action, tool: fc.tool, timestamp: fc.timestamp }
        return { ...state, touchedFiles: updated }
      }
      return { ...state, touchedFiles: [...state.touchedFiles, fc] }
    }

    case 'CLEAR_TOUCHED_FILES':
      return { ...state, touchedFiles: [] }

    case 'OPEN_DIFF_VIEWER':
      return { ...state, diffViewerFile: action.payload }

    case 'CLOSE_DIFF_VIEWER':
      return { ...state, diffViewerFile: null }

    case 'OPEN_CONFIRM':
      return { ...state, confirmDialog: action.payload }

    case 'CLOSE_CONFIRM':
      return { ...state, confirmDialog: null }

    default:
      return state
  }
}
