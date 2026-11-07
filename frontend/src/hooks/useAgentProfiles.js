import { useEffect } from 'react'
import { useApp } from '../context/AppContext'
import { listAgents } from '../api'
export default function useAgentProfiles() {
  const { state, dispatch } = useApp()
  useEffect(() => {
    let active = true
    const load = () => listAgents().then(profiles => { if (active) dispatch({ type: 'SET_AGENT_PROFILES', payload: profiles }) }).catch(console.error)
    load()
    window.addEventListener('focus', load)
    return () => { active = false; window.removeEventListener('focus', load) }
  }, [state.sidebarView, dispatch])
  return state.agentProfiles
}
