import { AppProvider } from './context/AppContext'
import Sidebar from './components/Sidebar/Sidebar'
import TopBar from './components/TopBar'
import Stage from './components/Stage/Stage'
import RunStatusDock from './components/Stage/RunStatusDock'
import PlanPanel from './components/Stage/PlanPanel'
import Composer from './components/Composer/Composer'
import QueuedMessages from './components/Composer/QueuedMessages'
import DirectoryPicker from './components/Modals/DirectoryPicker'
import ProviderPicker from './components/Modals/ProviderPicker'
import ConfirmDialog from './components/Modals/ConfirmDialog'
import SearchModal from './components/Modals/SearchModal'
import SettingsModal from './components/Modals/SettingsModal'
import WorkspacePanel from './components/Workspace/WorkspacePanel'
import DiffViewer from './components/Workspace/DiffViewer'
import { useApp } from './context/AppContext'
import useResizablePanel from './hooks/useResizablePanel'
import { useEffect } from 'react'

function AppLayout() {
  const { state, dispatch } = useApp()
  const { width: sidebarW, handleProps: sidebarHandle } = useResizablePanel(300, 200, 600, 'left')
  const editorOpen = !!state.workspaceEditorFile
  // Code needs more room than the file tree does, so lift the drag ceiling
  // while the editor owns the panel.
  const { width: workspaceW, handleProps: workspaceHandle } = useResizablePanel(
    320, 240, editorOpen ? 900 : 560, 'right',
  )

  useEffect(() => {
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        dispatch({ type: 'OPEN_SEARCH' })
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [dispatch])

  const wpOpen = state.workspacePanelOpen
  const editorExpanded = editorOpen && state.workspaceEditorExpanded
  const cols = wpOpen && !editorExpanded
    ? `${sidebarW}px minmax(400px, 1fr) ${workspaceW}px`
    : `${sidebarW}px minmax(480px, 1fr)`

  return (
    <div
      className="relative grid h-screen overflow-hidden"
      style={{ gridTemplateColumns: cols }}
    >
      <aside className="sidebar-desktop glass-panel relative flex flex-col border-r border-line overflow-hidden">
        <Sidebar />
        <div
          {...sidebarHandle}
          className="absolute top-0 right-0 w-1.5 h-full cursor-col-resize hover:bg-accent/40 transition-colors"
        />
      </aside>

      <main className="main-area flex flex-col overflow-hidden">
        <TopBar />
        <Stage />
        <PlanPanel />
        <RunStatusDock />
        <QueuedMessages />
        <Composer />
      </main>

      {wpOpen && (
        <aside
          className={
            editorExpanded
              ? 'glass-panel absolute inset-y-0 right-0 left-0 z-40 flex flex-col overflow-hidden'
              : 'glass-panel relative flex flex-col border-l border-line overflow-hidden'
          }
        >
          {!editorExpanded && (
            <div
              {...workspaceHandle}
              className="absolute top-0 left-0 w-1.5 h-full cursor-col-resize hover:bg-accent/40 transition-colors"
            />
          )}
          <WorkspacePanel />
        </aside>
      )}

      {state.diffViewerFile && <DiffViewer />}
      {state.showDirectoryPicker && <DirectoryPicker />}
      {state.showProviderPicker && <ProviderPicker />}
      {state.showSearch && <SearchModal />}
      {state.showSettings && <SettingsModal />}
      {state.confirmDialog && <ConfirmDialog />}
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <AppLayout />
    </AppProvider>
  )
}
