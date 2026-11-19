import { useApp } from '../../context/AppContext'
import AgentRunPanel from '../Agents/AgentRunPanel'
import TouchedFilesPanel from './TouchedFilesPanel'
import ActivityLog from './ActivityLog'
import FileTree from './FileTree'
import DiagnosticsPanel from './DiagnosticsPanel'
import SourceControl from './SourceControl'
import UsagePanel from './UsagePanel'
import CommandsPanel from './CommandsPanel'
import AgentsHelpPanel from './AgentsHelpPanel'
import ScratchNotes from './ScratchNotes'
import FileEditor from './FileEditor'
import ArtifactViewer from './ArtifactViewer'
import { isDesktopApp } from '../../desktop'

// Tabs that show the current session's workspace. On an agent's page they
// would otherwise keep showing whatever project session was open before.
// Notes stays available there with Scratch only (no project views).
const SESSION_TABS = new Set(['run', 'changes', 'source', 'activity', 'files', 'usage', 'diagnostics'])

const TABS = [
  { id: 'changes', label: 'Changes' },
  { id: 'source', label: 'Git' },
  { id: 'activity', label: 'Activity' },
  { id: 'files', label: 'Files' },
  { id: 'usage', label: 'Usage' },
  { id: 'diagnostics', label: 'Diagnostics' },
  { id: 'commands', label: 'Commands' },
  { id: 'agents', label: 'Agents' },
  { id: 'notes', label: 'Notes' },
]

export default function WorkspacePanel() {
  const { state, dispatch } = useApp()
  const tab = state.workspacePanelTab
  const editorFile = state.workspaceEditorFile
  // On an agent's page, keep the previous session's workspace only when that
  // session started one of this agent's runs.
  const current = state.currentMeta
  const sessionHidden = !!state.agentHome && !(
    (current?.kind === 'agent_run' && current.agent === state.agentHome) ||
    Object.values(state.sessionsById).some(s =>
      s.kind === 'agent_run' && s.agent === state.agentHome && s.parent_session_id && s.parent_session_id === state.currentSessionId))
  const tabs = current?.kind === 'agent_run' && !sessionHidden ? [{ id: 'run', label: 'Run' }, ...TABS] : TABS
  if (state.artifactFile) return <ArtifactViewer key={state.artifactFile} path={state.artifactFile} />

  if (editorFile && isDesktopApp()) {
    return <FileEditor key={editorFile} path={editorFile} />
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex flex-wrap items-center gap-1 px-3 py-2 border-b border-line">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => dispatch({ type: 'SET_WORKSPACE_TAB', payload: t.id })}
            className={`px-2.5 py-1 text-[12px] font-medium rounded transition-colors ${
              tab === t.id
                ? 'bg-accent/15 text-accent'
                : 'text-muted hover:text-text-bright hover:bg-surface-hover'
            }`}
          >
            {t.label}
          </button>
        ))}
        <button
          onClick={() => dispatch({ type: 'TOGGLE_WORKSPACE_PANEL' })}
          className="ml-auto p-1 text-muted hover:text-text-bright transition-colors"
          title="Close panel"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sessionHidden && SESSION_TABS.has(tab) ? (
          <p className="p-4 text-[13px] text-muted">Agent pages have no workspace of their own. Open one of the agent's runs to see its files and activity.</p>
        ) : <>
        {tab === 'run' && <AgentRunPanel />}
        {tab === 'changes' && <TouchedFilesPanel />}
        {tab === 'source' && <SourceControl />}
        {tab === 'activity' && <ActivityLog />}
        {tab === 'files' && <FileTree />}
        {tab === 'usage' && <UsagePanel />}
        {tab === 'diagnostics' && <DiagnosticsPanel />}
        {tab === 'commands' && <CommandsPanel />}
        </>}
        {tab === 'notes' && <ScratchNotes scratchOnly={sessionHidden} />}
        {tab === 'agents' && <AgentsHelpPanel />}
      </div>
    </div>
  )
}
