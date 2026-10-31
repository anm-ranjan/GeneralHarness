import { useEffect, useSyncExternalStore } from 'react'
import { attentionCount } from '../runStates'

// Number of threads needing the user (finished unseen, or waiting for an
// approval or answer) on the Dock/taskbar icon in the desktop app, and as a
// "(n)" prefix on the page title everywhere. Opening a thread, or focusing the
// window while it is open, clears its finished mark.
export default function useAttentionBadge(store) {
  const count = useSyncExternalStore(
    store.subscribe,
    () => attentionCount(store.getState().runStates, store.getState().unseenFinished),
    () => 0,
  )
  const appName = useSyncExternalStore(store.subscribe, () => store.getState().appName, () => 'MyHarness')

  useEffect(() => {
    const name = appName || 'MyHarness'
    document.title = count > 0 ? `(${count}) ${name}` : name
    try {
      window.myharnessDesktop?.setBadgeCount?.(count)
    } catch {
      // Older desktop shells have no badge bridge.
    }
  }, [count, appName])

  useEffect(() => {
    const seen = () => {
      if (document.visibilityState === 'visible') store.dispatch({ type: 'WINDOW_FOCUSED' })
    }
    window.addEventListener('focus', seen)
    document.addEventListener('visibilitychange', seen)
    return () => {
      window.removeEventListener('focus', seen)
      document.removeEventListener('visibilitychange', seen)
    }
  }, [store])
}
