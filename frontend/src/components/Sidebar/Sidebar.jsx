import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useAppDispatch, useAppSelector, useAppStateRef } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import useSelectSession from '../../hooks/useSelectSession'
import { importSessionBackup, isAbandonedRequest, downloadSessionBackup, downloadSessionExport } from '../../api'
import SearchInput from './SearchInput'
import HostSwitcher from './HostSwitcher'
import ProjectNode from './ProjectNode'
import SessionGroup from './SessionGroup'
import SessionItem from './SessionItem'
import SessionContextMenu from './SessionContextMenu'
import useStartChat from '../../hooks/useStartChat'
import { shortcutLabel, CHATS_PROJECT_ID } from '../../constants'
import { effectiveWorkspaceRoot } from '../../sessionWorkspace'
import { copyText } from '../../clipboard'
import {
  activitySessions, archivedSessions, filterOptions, isChatSession, labelsFor, matchesFilter,
  projectSessions, sessionReference, taskNamesFor,
} from '../../sessionLabels'
import MoveSessionModal from '../Modals/MoveSessionModal'
import LabelsModal from '../Modals/LabelsModal'

const selectProjects = state => state.projects
const selectSessionsById = state => state.sessionsById
const selectCurrentSessionId = state => state.currentSessionId
const selectAppName = state => state.appName
const selectActiveHostId = state => state.activeHostId
const selectSidebarView = state => state.sidebarView
const selectProjectNames = state => state.projectNames
const selectRunStates = state => state.runStates

function sameLabels(a, b) {
  return a && b && a.length === b.length && a.every((label, i) => label === b[i])
}

export default function Sidebar() {
  const dispatch = useAppDispatch()
  const stateRef = useAppStateRef()
  const projects = useAppSelector(selectProjects)
  const sessionsById = useAppSelector(selectSessionsById)
  const currentSessionId = useAppSelector(selectCurrentSessionId)
  const appName = useAppSelector(selectAppName)
  const activeHostId = useAppSelector(selectActiveHostId)
  const view = useAppSelector(selectSidebarView)
  const projectNames = useAppSelector(selectProjectNames)
  const runStates = useAppSelector(selectRunStates)
  const tree = useSessionTree()
  const selectSession = useSelectSession()
  const startChat = useStartChat()
  const [search, setSearch] = useState('')
  const [menu, setMenu] = useState(null)
  const [moveRequest, setMoveRequest] = useState(null)
  const [labelsSession, setLabelsSession] = useState(null)
  const [renameSessionId, setRenameSessionId] = useState(null)

  // Reloads on a host switch: the tree belongs to one machine, and
  // SWITCH_HOST has already emptied the previous host's copy.
  useEffect(() => {
    tree.loadTree().catch(err => {
      if (isAbandonedRequest(err)) return
      dispatch({ type: 'HOST_SWITCH_FAILED' })
      console.error('Failed to load tree:', err)
    })
  }, [activeHostId])

  const reportError = useCallback((err) => {
    dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || String(err) } })
  }, [dispatch])

  const onSelectSession = useCallback((sid) => {
    selectSession(sid).catch(err => console.error('Failed to select session:', err))
  }, [selectSession])

  const taskNamesByProject = useMemo(() => taskNamesFor(projects), [projects])

  // Keep each row's label array identity stable while its labels are unchanged,
  // so memoized rows skip re-rendering on unrelated tree updates.
  const labelsCache = useRef({})
  const labelsById = useMemo(() => {
    const next = {}
    for (const s of Object.values(sessionsById)) {
      const labels = labelsFor(s, taskNamesByProject)
      if (!labels.length) continue
      next[s.id] = sameLabels(labelsCache.current[s.id], labels) ? labelsCache.current[s.id] : labels
    }
    labelsCache.current = next
    return next
  }, [sessionsById, taskNamesByProject])

  // Existing labels are offered as suggestions in the labels editor; the
  // search box also matches labels.
  const options = useMemo(() => filterOptions(sessionsById, taskNamesByProject), [sessionsById, taskNamesByProject])

  const filter = useMemo(() => ({ query: search.trim() }), [search])
  const filtering = !!filter.query

  const createSession = useCallback(async (projectId, provider) => {
    const state = stateRef.current
    const providers = [
      state.nativeEnabled && 'native',
      state.codexAppServerEnabled && 'codex-app-server',
      state.claudeAgentEnabled && 'claude-agent',
    ].filter(Boolean)
    if (!provider && providers.length !== 1) {
      dispatch({ type: 'OPEN_PROVIDER_PICKER', payload: { projectId, taskId: '' } })
      return
    }
    try {
      const meta = await tree.createSession(projectId, '', '', provider || providers[0])
      await selectSession(meta.id)
    } catch (err) {
      console.error('Failed to create session:', err)
    }
  }, [tree, selectSession, stateRef, dispatch])

  const deleteSession = useCallback(async (sessionId) => {
    try {
      await tree.deleteSession(sessionId)
      if (stateRef.current.currentSessionId === sessionId) {
        dispatch({ type: 'CLEAR_SESSION' })
      }
    } catch (err) {
      reportError(err)
    }
  }, [tree, stateRef, dispatch, reportError])

  const importSession = useCallback((projectId) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.zip,application/zip'
    input.onchange = async () => {
      const file = input.files && input.files[0]
      if (!file) return
      try {
        const res = await importSessionBackup(file, projectId, '')
        await tree.loadTree()
        await selectSession(res.session_id)
      } catch (err) {
        reportError({ ...err, message: err.detail || err.message || 'Import failed' })
      }
    }
    input.click()
  }, [tree, selectSession, reportError])

  const moveSession = useCallback(async (sessionId, projectId) => {
    const meta = await tree.moveSession(sessionId, projectId)
    const state = stateRef.current
    if (state.currentSessionId === sessionId) {
      dispatch({ type: 'UPDATE_SESSION_META', payload: meta })
      const project = state.projects.find(p => p.id === projectId)
      dispatch({ type: 'SET_WORKSPACE_ROOT', payload: { root: effectiveWorkspaceRoot(meta, project?.root || ''), workingDirectory: meta.working_directory } })
    }
    return meta
  }, [tree, stateRef, dispatch])

  const dropSession = useCallback((sessionId, projectId) => {
    const session = stateRef.current.sessionsById[sessionId]
    if (!session || session.project_id === projectId || isChatSession(session)) return
    moveSession(sessionId, projectId).catch(reportError)
  }, [stateRef, moveSession, reportError])

  const renameSession = useCallback((sessionId, title) => {
    tree.renameSession(sessionId, title).catch(reportError)
  }, [tree, reportError])

  const openContextMenu = useCallback((session, x, y) => setMenu({ session, x, y }), [])
  const closeContextMenu = useCallback(() => setMenu(null), [])
  const clearRenameRequest = useCallback(() => setRenameSessionId(null), [])

  const menuItems = useMemo(() => {
    if (!menu) return []
    const s = sessionsById[menu.session.id] || menu.session
    const busy = !!(runStates[s.id] || s.status === 'running')
    const chat = isChatSession(s)
    return [
      { label: 'Copy thread reference', onSelect: () => copyText(sessionReference(s, projectNames)).catch(reportError) },
      { label: 'Rename', onSelect: () => setRenameSessionId(s.id) },
      { label: 'Labels…', onSelect: () => setLabelsSession(s) },
      ...(!chat ? [{ label: 'Move to project…', disabled: busy, onSelect: () => setMoveRequest({ session: s, projectId: s.project_id }) }] : []),
      'separator',
      { label: 'Export as Markdown', onSelect: () => downloadSessionExport(s.id, 'md').catch(reportError) },
      { label: 'Export backup (.zip)', onSelect: () => downloadSessionBackup(s.id).catch(reportError) },
      'separator',
      s.archived
        ? { label: 'Unarchive', onSelect: () => tree.updateSession(s.id, { archived: false }).catch(reportError) }
        : { label: 'Archive', disabled: busy, title: busy ? 'Wait for the run to finish' : '', onSelect: () => tree.updateSession(s.id, { archived: true }).catch(reportError) },
      {
        label: 'Delete…',
        tone: 'danger',
        disabled: busy,
        onSelect: () => dispatch({
          type: 'OPEN_CONFIRM',
          payload: {
            title: 'Delete thread?',
            message: 'This deletes the thread event stream and attachments. Archive it instead to keep it.',
            detail: s.title || s.id,
            confirmLabel: 'Delete',
            tone: 'danger',
            onConfirm: () => deleteSession(s.id),
          },
        }),
      },
    ]
  }, [menu, sessionsById, runStates, projectNames, tree, dispatch, deleteSession, reportError])

  const chatsProject = projects.find(p => p.id === CHATS_PROJECT_ID)
  const realProjects = useMemo(() => projects.filter(p => p.id !== CHATS_PROJECT_ID), [projects])

  const projectGroups = useMemo(() => realProjects.map(project => {
    const all = projectSessions(project, sessionsById)
    const nameMatches = !!filter.query && project.name.toLowerCase().includes(filter.query.toLowerCase())
    const sessions = !filtering ? all : all.filter(s =>
      matchesFilter(s, nameMatches ? { ...filter, query: '' } : filter, taskNamesByProject, project.name))
    return { project, sessions, total: all.length, visible: !filtering || sessions.length > 0 || nameMatches }
  }), [realProjects, sessionsById, filter, filtering, taskNamesByProject])

  const chatSessions = useMemo(() => (chatsProject ? projectSessions(chatsProject, sessionsById) : [])
    .filter(s => !filtering || matchesFilter(s, filter, taskNamesByProject, 'Chats')), [chatsProject, sessionsById, filter, filtering, taskNamesByProject])

  const activity = useMemo(() => activitySessions(sessionsById)
    .filter(s => !filtering || matchesFilter(s, filter, taskNamesByProject, projectNames[s.project_id] || '')), [sessionsById, filter, filtering, taskNamesByProject, projectNames])

  const subtitles = useMemo(() => {
    const out = {}
    for (const s of Object.values(sessionsById)) out[s.id] = isChatSession(s) ? 'Chat' : (projectNames[s.project_id] || s.project_id)
    return out
  }, [sessionsById, projectNames])

  const archived = useMemo(() => archivedSessions(sessionsById)
    .filter(s => !filtering || matchesFilter(s, filter, taskNamesByProject, projectNames[s.project_id] || '')), [sessionsById, filter, filtering, taskNamesByProject, projectNames])

  const rowProps = {
    labelsById,
    currentSessionId,
    onSelectSession,
    onRenameSession: renameSession,
    onSessionContextMenu: openContextMenu,
    renameSessionId,
    onRenameHandled: clearRenameRequest,
  }


  return (
    <div className="flex flex-col h-full pl-6 pr-4 pt-7 pb-5">
      <div className="flex items-center gap-2 mb-5">
        <h1 className="font-display text-[19px] font-bold leading-none text-text-bright">{appName}</h1>
        <button
          onClick={() => dispatch({ type: 'CLEAR_SESSION' })}
          disabled={!currentSessionId}
          className="p-1 rounded text-faint hover:text-accent hover:bg-surface-hover transition-colors disabled:opacity-40 disabled:hover:text-faint disabled:hover:bg-transparent"
          title="Home"
          aria-label="Home"
        >
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M2.5 7.5 8 2.5l5.5 5" />
            <path d="M4 6.8V13h3V9.5h2V13h3V6.8" />
          </svg>
        </button>
        <button
          onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
          className="ml-auto p-1 rounded text-faint hover:text-accent hover:bg-surface-hover transition-colors"
          title={`Hide sidebar (${shortcutLabel('B')})`}
          aria-label="Hide sidebar"
        >
          <SidebarIcon />
        </button>
      </div>

      <button
        onClick={() => dispatch({ type: 'OPEN_DIR_PICKER' })}
        className="w-full text-left px-2 py-2.5 mb-3 rounded-lg text-[13.5px] font-bold text-text-bright hover:bg-surface-hover transition-colors"
      >
        Add project
      </button>

      <SearchInput value={search} onChange={setSearch} />

      <div className="mt-3 mb-4 flex items-center gap-1 text-[12.5px] text-faint" role="tablist" aria-label="Sort threads by">
        <span className="mr-1">Sort by</span>
        <ViewButton active={view === 'projects'} label="Sort by project" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'projects' })}>
          <FolderIcon />
        </ViewButton>
        <ViewButton active={view === 'activity'} label="Sort by most recent activity" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'activity' })}>
          <ClockIcon />
        </ViewButton>
      </div>

      <div className="flex-1 overflow-y-auto -ml-2 -mr-2 pl-2 pr-2">
        {view === 'activity' ? (
          activity.length === 0 ? (
            <p className="text-[13px] text-faint italic text-center mt-8 mb-4">{filtering ? 'No matching threads.' : 'No threads yet.'}</p>
          ) : (
            <div className="space-y-px mb-3">
              {activity.map(s => (
                <SessionItem
                  key={s.id}
                  session={s}
                  labels={labelsById[s.id]}
                  subtitle={subtitles[s.id]}
                  isActive={s.id === currentSessionId}
                  onSelect={onSelectSession}
                  onRename={renameSession}
                  onContextMenu={openContextMenu}
                  renameRequested={renameSessionId === s.id}
                  onRenameDone={clearRenameRequest}
                />
              ))}
            </div>
          )
        ) : (
          <>
            {realProjects.length === 0 ? (
              <p className="text-[13px] text-faint italic text-center mt-8 mb-4">No projects yet. Use "Add project" above to start.</p>
            ) : !projectGroups.some(g => g.visible) ? (
              <p className="text-[13px] text-faint italic text-center mt-8 mb-4">No matching threads.</p>
            ) : (
              projectGroups.filter(g => g.visible).map(({ project, sessions, total }) => (
                <ProjectNode
                  key={project.id}
                  project={project}
                  sessions={sessions}
                  totalCount={total}
                  filtering={filtering}
                  onRenameProject={tree.renameProject}
                  onDeleteProject={pid => tree.deleteProject(pid).catch(reportError)}
                  onCreateSession={createSession}
                  onImportSession={importSession}
                  onDropSession={dropSession}
                  {...rowProps}
                />
              ))
            )}

            {(!filtering || chatSessions.length > 0) && (
              <SessionGroup
                title="Chats"
                sessions={chatSessions}
                emptyText="No chats yet."
                filtering={filtering}
                action={<button onClick={() => startChat()} className="text-faint hover:text-accent text-[15px] leading-none px-1" title="New chat" aria-label="New chat">+</button>}
                {...rowProps}
              />
            )}
          </>
        )}

        {archived.length > 0 && (
          <div className="mt-3 pt-2 border-t border-line/60">
            <SessionGroup
              title="Archived"
              sessions={archived}
              subtitles={subtitles}
              emptyText="Nothing archived."
              defaultCollapsed
              filtering={filtering}
              muted
              {...rowProps}
            />
          </div>
        )}
      </div>

      <HostSwitcher />

      {menu && <SessionContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={closeContextMenu} />}

      {moveRequest && (
        <MoveSessionModal
          moveRequest={moveRequest}
          projects={projects}
          onClose={() => setMoveRequest(null)}
          onMove={moveSession}
        />
      )}

      {labelsSession && (
        <LabelsModal
          session={{ ...labelsSession, labels: labelsFor(labelsSession, taskNamesByProject) }}
          suggestions={options.labels}
          onClose={() => setLabelsSession(null)}
          onSave={labels => tree.updateSession(labelsSession.id, { labels })}
        />
      )}
    </div>
  )
}

function ViewButton({ active, label, onClick, children }) {
  return (
    <button
      role="tab"
      aria-selected={active}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`p-1 rounded transition-colors ${active ? 'text-accent bg-accent-soft' : 'hover:text-accent hover:bg-surface-hover'}`}
    >
      {children}
    </button>
  )
}

function FolderIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  )
}

function ClockIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  )
}

export function SidebarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  )
}
