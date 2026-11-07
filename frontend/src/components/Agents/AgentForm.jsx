import { useEffect, useState } from 'react'
import DirectoryPicker from '../Modals/DirectoryPicker'
import { getAgentOptions } from '../../api'
import { toRequest } from '../../agentForm'
import { errorLines, fieldLabel } from '../../agentUi'

const control = 'w-full rounded-lg border bg-transparent px-3 py-2 text-[13.5px] text-text-default outline-none transition-colors placeholder:text-faint focus:border-accent'

function lines(value) {
  return String(value || '').split('\n').map(v => v.trim()).filter(Boolean)
}

// `options_from: script`: a select filled by the agent's script, reloaded
// when an `options_depends` value changes. The value stays free text, so if
// listing fails the field falls back to a text input.
function ScriptOptionsField({ agent, name, spec, id, className, value, dependsKey, depends, onChange }) {
  const [state, setState] = useState({ loading: true, options: [], error: '' })
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let active = true
    setState(old => ({ ...old, loading: true }))
    const timer = setTimeout(() => {
      getAgentOptions(agent, name, depends)
        .then(r => { if (active) setState({ loading: false, options: r.options || [], error: r.error || '' }) })
        .catch(e => { if (active) setState({ loading: false, options: [], error: errorLines(e).join(' ') }) })
    }, nonce ? 0 : 400)
    return () => { active = false; clearTimeout(timer) }
    // dependsKey stands for `depends`; reload only when those values change.
  }, [agent, name, dependsKey, nonce])
  const { loading, options, error } = state
  const usable = !error && options.length > 0
  const listed = usable && value && !options.some(o => o.value === value) ? [{ value, label: `${value} (not listed)` }, ...options] : options
  const reload = (
    <button type="button" onClick={() => setNonce(n => n + 1)} disabled={loading} title="Reload the list"
      className="shrink-0 rounded-lg border border-line px-3 py-2 text-[12.5px] text-text-default transition-colors hover:border-accent hover:text-accent disabled:opacity-40">
      {loading ? 'Loading…' : 'Reload'}
    </button>
  )
  return (
    <>
      <div className="flex items-start gap-2">
        {usable ? (
          <select id={id} className={className} value={value} onChange={e => onChange(e.target.value)}>
            <option value="">{spec.required ? 'Choose…' : 'Any'}</option>
            {listed.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ) : (
          <input id={id} className={className} value={value} onChange={e => onChange(e.target.value)} placeholder={loading ? 'Loading choices…' : ''} />
        )}
        {reload}
      </div>
      {!loading && error && <p className="mt-1 text-[12px] text-warn">Couldn't load the list: {error} You can still type a value.</p>}
      {!loading && !error && !options.length && <p className="mt-1 text-[12px] text-faint">The agent returned no choices. You can still type a value.</p>}
    </>
  )
}

// Generated from the manifest's params, in file order. Values stay strings
// (lists one per line) until agentForm.toRequest converts them.
export default function AgentForm({ agent, params, values, onChange, errors = {} }) {
  const [picker, setPicker] = useState(null)
  const spec = picker ? params[picker] : null
  return (
    <div className="grid gap-5">
      {picker && (
        <DirectoryPicker
          kind={spec.kind || 'dir'}
          multiple={spec.type === 'paths'}
          selected={spec.type === 'paths' ? lines(values[picker]) : []}
          onClose={() => setPicker(null)}
          onSelect={path => onChange(picker, spec.type === 'paths' ? [...lines(values[picker]), path].join('\n') : path)}
        />
      )}
      {Object.entries(params).map(([name, p]) => {
        const id = `agent-field-${name}`
        const fieldErrors = errors[name] || []
        const border = fieldErrors.length ? 'border-danger' : 'border-line'
        const isPath = ['path', 'paths'].includes(p.type)
        const count = p.type === 'paths' || p.type === 'strings' ? lines(values[name]).length : 0
        return (
          <div key={name}>
            {p.type === 'bool' ? (
              <label htmlFor={id} className="flex items-center gap-2.5 text-[13.5px] text-text-default">
                <input id={id} type="checkbox" className="h-4 w-4 accent-[var(--color-accent)]" checked={!!values[name]} onChange={e => onChange(name, e.target.checked)} />
                {fieldLabel(name, p)}
              </label>
            ) : (
              <>
                <div className="mb-1.5 flex items-baseline justify-between gap-3">
                  <label htmlFor={id} className="text-[13px] font-medium text-text-default">
                    {fieldLabel(name, p)}{p.required && <span className="ml-0.5 text-accent" aria-label="required">*</span>}
                  </label>
                  {count > 0 && <span className="text-[11.5px] text-faint">{count} {count === 1 ? 'entry' : 'entries'}</span>}
                </div>
                <div className={isPath ? 'flex items-start gap-2' : ''}>
                  {p.options_from === 'script' ? (() => {
                    const depends = Object.fromEntries(Object.entries(toRequest(params, values)).filter(([k]) => (p.options_depends || []).includes(k)))
                    return <ScriptOptionsField agent={agent} name={name} spec={p} id={id} className={`${control} ${border}`} value={values[name] ?? ''}
                      depends={depends} dependsKey={JSON.stringify(depends)} onChange={v => onChange(name, v)} />
                  })() : p.type === 'enum' ? (
                    <select id={id} className={`${control} ${border}`} value={values[name]} onChange={e => onChange(name, e.target.value)}>
                      {!p.required || values[name] === '' ? <option value="">{p.required ? 'Choose…' : 'Default'}</option> : null}
                      {p.options.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : ['paths', 'strings', 'text'].includes(p.type) ? (
                    <textarea
                      id={id}
                      className={`${control} ${border} font-mono text-[12.5px]`}
                      rows={p.type === 'text' ? 3 : Math.min(8, Math.max(3, count + 1))}
                      placeholder={p.type === 'text' ? '' : p.type === 'paths' ? 'One path per line, or use Browse' : 'One value per line'}
                      value={values[name]}
                      onChange={e => onChange(name, e.target.value)}
                    />
                  ) : (
                    <input
                      id={id}
                      className={`${control} ${border} ${isPath ? 'font-mono text-[12.5px]' : ''}`}
                      type={['int', 'float'].includes(p.type) ? 'number' : 'text'}
                      step={p.type === 'float' ? 'any' : p.type === 'int' ? 1 : undefined}
                      min={p.min}
                      max={p.max}
                      value={values[name]}
                      onChange={e => onChange(name, e.target.value)}
                    />
                  )}
                  {isPath && (
                    <button type="button" onClick={() => setPicker(name)} className="shrink-0 rounded-lg border border-line px-3 py-2 text-[12.5px] text-text-default transition-colors hover:border-accent hover:text-accent">
                      {p.type === 'paths' ? 'Add…' : 'Browse…'}
                    </button>
                  )}
                </div>
              </>
            )}
            {(p.help || p.min != null || p.max != null) && !fieldErrors.length && (
              <p className="mt-1 text-[12px] text-faint">
                {p.help}{p.help && (p.min != null || p.max != null) ? ' · ' : ''}
                {p.min != null && p.max != null ? `${p.min}–${p.max}` : p.min != null ? `at least ${p.min}` : p.max != null ? `at most ${p.max}` : ''}
              </p>
            )}
            {fieldErrors.map((e, n) => <p key={n} role="alert" className="mt-1 text-[12px] text-danger">{e}</p>)}
          </div>
        )
      })}
    </div>
  )
}
