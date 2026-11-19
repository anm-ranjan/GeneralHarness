import { useApp } from '../../context/AppContext'
import useAgentProfiles from '../../hooks/useAgentProfiles'
import { paramSummary } from '../../agentUi'

// Workspace > Agents: a reference for routine agents, like Commands is for
// slash commands. Routine agents are distinct from the native subagents shown
// in Stage/AgentsPanel.
const HOW_TO = [
  ['@name request', 'In any session\'s composer, starts a separate run of that agent right away, even while the session is busy. The session gets a card linking to the run.'],
  ['Agents view', 'The third sidebar view. Click an agent for its form and recent runs, or a run for its transcript.'],
  ['Stop', 'Stops only that run. It never undoes work the script already handed to other systems.'],
  ['/create_agent <intent>', 'Drafts a new agent for you to review, dry-run and save. Nothing is installed automatically.'],
]

export default function AgentsHelpPanel() {
  const { state, dispatch } = useApp()
  const profiles = useAgentProfiles()
  const sessionId = state.currentSessionId
  const canInsert = !!sessionId && !state.agentHome

  const insert = name => {
    if (canInsert) dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId, text: '@' + name + ' ', replace: true } })
  }

  return (
    <div className="space-y-4 p-3 text-[13px]">
      <section className="space-y-2">
        {HOW_TO.map(([usage, text]) => (
          <div key={usage}>
            <p className="font-mono text-[12px] text-text-bright">{usage}</p>
            <p className="leading-snug text-muted">{text}</p>
          </div>
        ))}
      </section>
      <section>
        <h3 className="mb-1 text-[12px] font-medium uppercase tracking-wide text-faint">Available agents</h3>
        {canInsert && <p className="mb-2 text-[12px] text-muted">Click @name to put it in the composer.</p>}
        {!profiles.length && <p className="text-[12px] text-muted italic">No agents found.</p>}
        <ul className="space-y-2">
          {profiles.map(p => {
            const reasons = p.disabled_reasons || p.errors || []
            const blocked = p.errors.length > 0 || p.ready === false
            const params = Object.entries(p.params || {})
            return (
              <li key={p.name} className={`rounded-lg border border-line p-2.5 ${blocked ? 'opacity-80' : ''}`}>
                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    disabled={!canInsert || blocked}
                    onClick={() => insert(p.name)}
                    title={blocked ? 'Not ready on this machine' : canInsert ? 'Put in composer' : 'Open a session first'}
                    className="rounded bg-surface-hover px-1.5 py-0.5 font-mono text-[12px] text-text-bright hover:text-accent disabled:cursor-default disabled:hover:text-text-bright"
                  >@{p.name}</button>
                  <button type="button" onClick={() => dispatch({ type: 'OPEN_AGENT_HOME', payload: p.name })} className="text-[12px] text-accent hover:underline">Open</button>
                  {blocked && <span className="text-[11.5px] text-warn">⚠ not ready</span>}
                </div>
                <p className="mt-1.5 leading-snug text-muted">{p.description}</p>
                {params.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5 text-[12px]">
                    {params.map(([name, spec]) => (
                      <li key={name}><span className="font-mono text-text-default">{name}</span> <span className="text-faint">{paramSummary(spec)}</span></li>
                    ))}
                  </ul>
                )}
                <p className="mt-1.5 text-[11.5px] text-faint">
                  {[p.has_entry ? 'Runs a script' : 'Instructions only', p.dry_run && 'dry run', p.uses_skills?.length && 'skills: ' + p.uses_skills.join(', '), p.platform?.length && 'only on ' + p.platform.join(', ')].filter(Boolean).join(' · ')}
                </p>
                {blocked && reasons.length > 0 && <ul className="mt-1 list-disc pl-4 text-[12px] text-warn">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
              </li>
            )
          })}
        </ul>
      </section>
    </div>
  )
}
