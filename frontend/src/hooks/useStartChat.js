import { useCallback } from 'react'
import { useApp } from '../context/AppContext'
import useSessionTree from './useSessionTree'
import useSelectSession from './useSelectSession'

// Starts a general-purpose chat. When another provider is available it opens the
// provider picker in chat mode; otherwise it creates a native chat directly and
// selects it. Shared by the splash screen and the sidebar Chats header.
export default function useStartChat() {
  const { state, dispatch } = useApp()
  const tree = useSessionTree()
  const selectSession = useSelectSession()

  return useCallback(async (provider) => {
    if (!provider && (state.codexAppServerEnabled || state.claudeAgentEnabled)) {
      dispatch({ type: 'OPEN_PROVIDER_PICKER', payload: { mode: 'chat' } })
      return
    }
    try {
      const meta = await tree.createChat(provider || 'native')
      await selectSession(meta.id)
    } catch (err) {
      console.error('Failed to create chat:', err)
    }
  }, [tree, selectSession, state.codexAppServerEnabled, state.claudeAgentEnabled, dispatch])
}
