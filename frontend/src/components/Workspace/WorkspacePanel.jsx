import { useApp } from '../../context/AppContext'
import TouchedFilesPanel from './TouchedFilesPanel'
import ActivityLog from './ActivityLog'
import FileTree from './FileTree'
import DiagnosticsPanel from './DiagnosticsPanel'
import SourceControl from './SourceControl'
import UsagePanel from './UsagePanel'
import FileEditor from './FileEditor'
import { isDesktopApp } from '../../desktop'

const TABS = [
  { id: 'changes', label: 'Changes' },
  { id: 'source', label: 'Git' },
  { id: 'activity', label: 'Activity' },
  { id: 'files', label: 'Files' },
  { id: 'usage', label: 'Usage' },
  { id: 'diagnostics', label: 'Diagnostics' },
]

export default function WorkspacePanel() {
  const { state, dispatch } = useApp()
  const tab = state.workspacePanelTab
  const editorFile = state.workspaceEditorFile

  if (editorFile && isDesktopApp()) {
    return <FileEditor key={editorFile} path={editorFile} />
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1 px-3 py-2 border-b border-line">
        {TABS.map(t => (
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
        {tab === 'changes' && <TouchedFilesPanel />}
        {tab === 'source' && <SourceControl />}
        {tab === 'activity' && <ActivityLog />}
        {tab === 'files' && <FileTree />}
        {tab === 'usage' && <UsagePanel />}
        {tab === 'diagnostics' && <DiagnosticsPanel />}
      </div>
    </div>
  )
}
