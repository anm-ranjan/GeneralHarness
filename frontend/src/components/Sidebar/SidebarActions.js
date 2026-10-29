import { createContext, useContext } from 'react'

// Session-row actions owned by the Sidebar (right-click menu, rename requests
// from that menu). Shared through context so ProjectNode, TaskRow and ChatsNode
// do not need to forward them.
export const SidebarActionsContext = createContext({
  openContextMenu: null,
  renameSessionId: null,
  clearRenameRequest: () => {},
})

export function useSidebarActions() {
  return useContext(SidebarActionsContext)
}
