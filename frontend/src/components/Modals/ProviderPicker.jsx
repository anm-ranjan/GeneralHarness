import { useApp } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import { api } from '../../api'
import { useState } from 'react'
import { effectiveWorkspaceRoot } from '../../sessionWorkspace'

export default function ProviderPicker() {
  const { state, dispatch } = useApp()
  const tree = useSessionTree()
  const { projectId, taskId, mode } = state.showProviderPicker || {}
  const isChat = mode === 'chat'
  const [selectingProvider, setSelectingProvider] = useState(null)

  async function selectProvider(provider) {
    setSelectingProvider(provider)
    try {
      const meta = isChat
        ? await tree.createChat(provider)
        : await tree.createSession(projectId, taskId, '', provider)
      const data = await api('GET', `/api/sessions/${encodeURIComponent(meta.id)}`)
      const project = state.projects.find(p => p.id === data.meta.project_id)
      dispatch({ type: 'SELECT_SESSION', payload: { meta: data.meta, workspaceRoot: effectiveWorkspaceRoot(data.meta, project?.root || '') } })
      dispatch({ type: 'CLOSE_PROVIDER_PICKER' })
    } catch (err) {
      console.error('Failed to create session:', err)
    } finally {
      setSelectingProvider(null)
    }
  }

  function requestProvider(provider) {
    selectProvider(provider)
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) dispatch({ type: 'CLOSE_PROVIDER_PICKER' })
  }

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center"
      onClick={handleOverlayClick}
    >
      <div className="glass-surface border border-line rounded-lg p-6 w-[320px]">
        <h3 className="text-[15px] font-semibold text-text-bright mb-4">Choose Provider</h3>
        <div className="space-y-2">
          <button
            onClick={() => requestProvider('native')}
            disabled={Boolean(selectingProvider)}
            className="w-full py-2.5 text-[13px] font-medium text-text-bright border border-line rounded-md hover:border-accent hover:text-accent transition-colors"
          >
            Native Agent
          </button>
          {state.claudeAgentEnabled && (
            <button
              onClick={() => requestProvider('claude-agent')}
              disabled={Boolean(selectingProvider)}
              className="w-full py-2.5 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
            >
              Claude
            </button>
          )}
          {state.codexAppServerEnabled && (
            <button
              onClick={() => requestProvider('codex-app-server')}
              disabled={Boolean(selectingProvider)}
              className="w-full py-2.5 text-[13px] font-medium text-ok border border-ok/30 rounded-md hover:bg-ok-soft transition-colors"
            >
              Codex App Server
            </button>
          )}
          <button
            onClick={() => dispatch({ type: 'CLOSE_PROVIDER_PICKER' })}
            disabled={Boolean(selectingProvider)}
            className="w-full py-2 text-[13px] text-muted border border-line rounded-md hover:bg-surface transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
