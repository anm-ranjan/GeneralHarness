import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react'
import { api } from '../api'
import { initialState, reducer } from './appReducer'
import useGlobalEvents from '../hooks/useGlobalEvents'

const AppContext = createContext(null)

// Minimal external store. Components subscribe to the slices they actually
// read (useAppSelector) instead of every consumer re-rendering on every
// action, which matters while assistant deltas stream in.
function createStore() {
  let state = initialState
  const listeners = new Set()
  return {
    getState: () => state,
    dispatch(action) {
      const next = reducer(state, action)
      if (next === state) return
      state = next
      listeners.forEach(listener => listener())
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

export function AppProvider({ children }) {
  const storeRef = useRef(null)
  if (storeRef.current === null) storeRef.current = createStore()
  const store = storeRef.current
  const dispatch = store.dispatch

  // Always-current view of the store for callbacks that must not re-subscribe.
  const stateRef = useMemo(() => ({ get current() { return store.getState() } }), [store])

  useGlobalEvents(dispatch, stateRef)

  useEffect(() => {
    let cancelled = false

    function checkHealth() {
      api('GET', '/api/health')
        .then(data => {
          if (!cancelled) {
            dispatch({ type: 'SET_HEALTH', payload: data })
            document.title = data.app_name || 'MyHarness'
          }
        })
        .catch(err => {
          if (!cancelled) {
            dispatch({ type: 'SET_SERVER_ONLINE', payload: false })
            console.error('Health check failed:', err)
          }
        })
    }

    checkHealth()
    const timer = setInterval(checkHealth, 5000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return (
    <AppContext.Provider value={store}>
      {children}
    </AppContext.Provider>
  )
}

function useStore() {
  const store = useContext(AppContext)
  if (!store) throw new Error('useApp must be used within AppProvider')
  return store
}

/** Stable dispatch. Components using only this never re-render on state changes. */
export function useAppDispatch() {
  return useStore().dispatch
}

/**
 * Subscribe to one slice of state. Re-renders only when the selected value
 * changes, compared with `isEqual` (Object.is by default).
 */
export function useAppSelector(selector, isEqual = Object.is) {
  const store = useStore()
  const selectorRef = useRef(selector)
  const isEqualRef = useRef(isEqual)
  selectorRef.current = selector
  isEqualRef.current = isEqual
  const cache = useRef({ state: null, selector: null, value: null, primed: false })

  const getSnapshot = useCallback(() => {
    const state = store.getState()
    const selectorFn = selectorRef.current
    const entry = cache.current
    // Recompute when either the state or the selector itself changed; an
    // inline selector closing over new props must not return a stale value.
    if (entry.primed && entry.state === state && entry.selector === selectorFn) {
      return entry.value
    }
    const next = selectorFn(state)
    // Keep the previous reference when the value is equivalent, so
    // useSyncExternalStore sees a stable snapshot and skips the re-render.
    if (entry.primed && isEqualRef.current(entry.value, next)) {
      entry.state = state
      entry.selector = selectorFn
      return entry.value
    }
    cache.current = { state, selector: selectorFn, value: next, primed: true }
    return next
  }, [store])

  return useSyncExternalStore(store.subscribe, getSnapshot, getSnapshot)
}

/** Whole-state access. Prefer useAppSelector/useAppDispatch in hot components. */
export function useApp() {
  const store = useStore()
  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState)
  return { state, dispatch: store.dispatch }
}

/** Non-reactive store handle for callbacks that need current state on demand. */
export function useAppStateRef() {
  const store = useStore()
  return useMemo(() => ({ get current() { return store.getState() } }), [store])
}
