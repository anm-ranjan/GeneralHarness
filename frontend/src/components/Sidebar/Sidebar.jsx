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
    <div className="flex flex-col h-full px-5 pt-8 pb-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-[18px] font-bold text-text-bright mb-0.5">{appName}</h1>
          <p className="text-[12px] text-faint mb-4">Projects & Threads</p>
        </div>
        <div className="mt-0.5 flex items-center gap-0.5" role="tablist" aria-label="Sidebar view">
          <ViewButton active={view === 'projects'} label="Projects" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'projects' })}>
            <BookIcon />
          </ViewButton>
          <ViewButton active={view === 'activity'} label="Activity — all threads by recent activity" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'activity' })}>
            <BellIcon />
          </ViewButton>
          <button
            onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
            className="p-1.5 rounded text-faint hover:text-accent hover:bg-surface-hover transition-colors"
            title={`Hide sidebar (${shortcutLabel('B')})`}
            aria-label="Hide sidebar"
          >
            <SidebarIcon />
          </button>
        </div>
      </div>

      <HostSwitcher />

      <SearchInput value={search} onChange={setSearch} />

      <button
        onClick={() => dispatch({ type: 'OPEN_SEARCH' })}
        className="mt-2 w-full py-1.5 text-[12px] font-medium text-muted border border-line rounded-md hover:text-accent hover:border-accent/30 hover:bg-accent-soft transition-colors flex items-center justify-center gap-1.5"
        title={`Search all transcripts (${shortcutLabel('K')})`}
      >
        <span>Search transcripts</span>
        <span className="text-[10px] text-faint">{shortcutLabel('K')}</span>
      </button>

      <div className="mt-2 mb-3 grid grid-cols-2 gap-2">
        <button
          onClick={() => startChat()}
          className="py-2 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
        >
          + Chat
        </button>
        <button
          onClick={() => dispatch({ type: 'OPEN_DIR_PICKER' })}
          className="py-2 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
        >
          + Project
        </button>
      </div>

      <div className="flex-1 overflow-y-auto -mx-1 px-1">
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
              <p className="text-[13px] text-faint italic text-center mt-8 mb-4">No projects yet. Click "+ Project" to start.</p>
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
                action={<button onClick={() => startChat()} className="text-faint hover:text-ok text-[11px] px-0.5" title="New chat">+</button>}
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
      className={`p-1.5 rounded transition-colors ${active ? 'text-accent bg-accent-soft' : 'text-faint hover:text-accent hover:bg-surface-hover'}`}
    >
      {children}
    </button>
  )
}

function BookIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Z" />
      <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" />
    </svg>
  )
}

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
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
