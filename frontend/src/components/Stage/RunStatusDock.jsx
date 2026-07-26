import { useApp } from '../../context/AppContext'
import CodexRunningBar from './CodexRunningBar'
import IterationMeter from './IterationMeter'

export default function RunStatusDock() {
  const { state } = useApp()
  if (!state.currentSessionId) return null
  if (!state.showCodexRunning && state.iterationN === null) return null

  return (
    <div className="border-t border-line px-7 py-2 bg-bg/95">
      <div className="max-w-[820px] mx-auto">
        {state.showCodexRunning && <CodexRunningBar />}
        {state.iterationN !== null && <IterationMeter n={state.iterationN} max={state.iterationMax} />}
      </div>
    </div>
  )
}
