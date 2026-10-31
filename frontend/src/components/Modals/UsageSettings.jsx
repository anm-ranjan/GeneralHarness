import { useEffect, useMemo, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'
import ProviderLogo from '../ProviderLogo'
import { costKindNote, formatCost, formatReset, formatTokens, providerLabel, usageTotals } from '../../usage'

const PROVIDER_IDS = { native: 'native', codex: 'codex-app-server', claude: 'claude-agent' }

function LimitBar({ label, percent, status, resetsAt }) {
  const known = typeof percent === 'number'
  const tone = status === 'rejected' || (known && percent >= 90)
    ? 'bg-danger' : status === 'allowed_warning' || (known && percent >= 70) ? 'bg-warn' : 'bg-accent'
  const statusText = known ? `${percent}% used`
    : status === 'rejected' ? 'Limit reached' : status === 'allowed_warning' ? 'Approaching limit' : status === 'allowed' ? 'OK' : 'No data yet'
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-3 text-[12px]">
        <span className="text-text-default">{label}</span>
        <span className="text-muted">{statusText}{resetsAt ? ` · resets ${formatReset(resetsAt)}` : ''}</span>
      </div>
      {known && (
        <div className="mt-1 h-1.5 rounded-full bg-surface-hover overflow-hidden" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={`${label} usage`}>
          <div className={`h-full ${tone}`} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
        </div>
      )}
    </div>
  )
}

function AccountRow({ id, info }) {
  if (!info?.available) return null
  const authLabel = info.auth === 'subscription' ? 'Subscription' : info.auth === 'api' ? 'API' : info.auth === 'cloud' ? 'Cloud' : 'Unknown'
  const plan = info.plan ? ` · ${info.plan.charAt(0).toUpperCase()}${info.plan.slice(1)}` : ''
  const windows = id === 'codex' ? (info.windows || []).map(w => ({
    key: w.label, label: w.label, percent: w.used_percent, resetsAt: w.resets_at,
  })) : id === 'claude' ? (info.limits || []).map(l => ({
    key: l.type, label: l.label, percent: l.used_percent, status: l.status, resetsAt: l.resets_at,
  })) : []
  return (
    <li className="py-3 first:pt-0">
      <div className="flex items-center gap-2 text-[13px]">
        <ProviderLogo provider={PROVIDER_IDS[id]} model={id === 'native' ? info.model : ''} />
        <span className="font-bold text-text-bright">{providerLabel(PROVIDER_IDS[id])}</span>
        <span className="text-muted">{authLabel}{plan}{info.login ? ` · ${info.login}` : ''}</span>
      </div>
      {info.error && <p className="mt-1 text-[12px] text-danger">{info.error}</p>}
      {id === 'native' && (
        <p className="mt-1 text-[12px] text-muted">Pay per request. Costs below are what OpenRouter charged.</p>
      )}
      {info.auth === 'subscription' && windows.length > 0 && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {windows.map(w => <LimitBar key={w.key} {...w} />)}
        </div>
      )}
      {id === 'claude' && info.auth === 'subscription' && !windows.some(w => typeof w.percent === 'number') && (
        <p className="mt-1.5 text-[12px] text-faint">
          The Claude SDK reports a percentage only near a limit. Status updates after each Claude run.
        </p>
      )}
      {id === 'codex' && info.reset_credits > 0 && (
        <p className="mt-1.5 text-[12px] text-faint">{info.reset_credits} free limit reset available.</p>
      )}
    </li>
  )
}

export default function UsageSettings() {
  const { state } = useApp()
  const [accounts, setAccounts] = useState(null)
  const [usage, setUsage] = useState(null)
  const [period, setPeriod] = useState('month')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(refresh = false) {
    setLoading(true)
    setError('')
    try {
      const [nextUsage, nextAccounts] = await Promise.all([
        api('GET', '/api/usage'),
        api('GET', `/api/usage/accounts${refresh ? '?refresh=true' : ''}`),
      ])
      setUsage(nextUsage)
      setAccounts(nextAccounts)
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not load usage.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [state.activeHostId])

  const selected = usage?.periods?.find(p => p.id === period)
  const groups = useMemo(() => {
    const byProvider = new Map()
    for (const row of selected?.rows || []) {
      if (!byProvider.has(row.provider)) byProvider.set(row.provider, [])
      byProvider.get(row.provider).push(row)
    }
    return [...byProvider.entries()]
  }, [selected])
  const totals = usageTotals(selected?.rows || [])

  return (
    <div className="space-y-6">
      <section>
        <div className="flex items-center gap-3 mb-2">
          <h4 className="text-[13px] font-bold text-text-bright">Accounts and limits</h4>
          <button
            onClick={() => load(true)}
            disabled={loading}
            className="ml-auto px-2.5 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        {error && <p className="text-[12px] text-danger mb-2" role="alert">{error}</p>}
        {accounts ? (
          <ul className="divide-y divide-line">
            {['native', 'codex', 'claude'].map(id => <AccountRow key={id} id={id} info={accounts[id]} />)}
          </ul>
        ) : !error && <p className="text-[13px] text-faint">Reading accounts…</p>}
      </section>

      <section>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <h4 className="text-[13px] font-bold text-text-bright">Usage</h4>
          <div className="flex gap-1 rounded-md border border-line bg-surface p-0.5" role="tablist" aria-label="Period">
            {(usage?.periods || []).map(p => (
              <button
                key={p.id}
                role="tab"
                aria-selected={period === p.id}
                onClick={() => setPeriod(p.id)}
                className={`rounded px-2.5 py-1 text-[11px] transition-colors ${period === p.id ? 'bg-accent-soft text-accent' : 'text-muted hover:text-text-bright'}`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3 text-[12px]">
          <div className="rounded-md border border-line px-3 py-2">
            <div className="text-muted">Charged (API)</div>
            <div className="text-[18px] font-bold text-text-bright">{formatCost(totals.charged)}</div>
          </div>
          <div className="rounded-md border border-line px-3 py-2" title="What subscription use would have cost at API prices">
            <div className="text-muted">Covered by plans</div>
            <div className="text-[18px] font-bold text-text-bright">{formatCost(totals.equivalent)}</div>
          </div>
          <div className="rounded-md border border-line px-3 py-2">
            <div className="text-muted">Tokens</div>
            <div className="text-[18px] font-bold text-text-bright">{formatTokens(totals.tokens)}</div>
          </div>
        </div>

        {groups.length === 0 ? (
          <p className="text-[13px] text-faint">No model calls in this period.</p>
        ) : (
          <table className="w-full text-[12px]">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 text-left font-medium">Model</th>
                <th className="py-1.5 text-right font-medium">Calls</th>
                <th className="py-1.5 text-right font-medium" title="Input tokens, of which read from cache">Input (cached)</th>
                <th className="py-1.5 text-right font-medium" title="Output tokens, of which reasoning">Output (reasoning)</th>
                <th className="py-1.5 text-right font-medium">Cost</th>
              </tr>
            </thead>
            {groups.map(([provider, rows]) => (
              <tbody key={provider}>
                <tr>
                  <td colSpan={5} className="pt-3 pb-1">
                    <span className="inline-flex items-center gap-1.5 font-bold text-text-bright">
                      <ProviderLogo provider={provider} model={rows[0]?.model || ''} className="h-3.5 w-3.5" />
                      {providerLabel(provider)}
                    </span>
                  </td>
                </tr>
                {rows.map(row => (
                  <tr key={`${row.model}|${row.auth}`} className="border-b border-line/60">
                    <td className="py-1.5 pr-2 font-mono text-[11.5px] text-text-default break-all">
                      <span className="inline-flex items-center gap-1.5">
                        {provider === 'native' && <ProviderLogo provider="native" model={row.model} className="h-3.5 w-3.5" />}
                        {row.model || 'Unknown model'}
                      </span>
                    </td>
                    <td className="py-1.5 text-right text-muted">{row.requests.toLocaleString()}</td>
                    <td className="py-1.5 text-right text-muted">{formatTokens(row.input_tokens)} ({formatTokens(row.cache_read_tokens)})</td>
                    <td className="py-1.5 text-right text-muted">{formatTokens(row.output_tokens)} ({formatTokens(row.reasoning_tokens)})</td>
                    <td className="py-1.5 text-right text-text-bright" title={costKindNote(row)}>
                      {row.cost_usd == null ? '—' : formatCost(row.cost_usd)}
                      {row.cost_kind !== 'reported' && row.cost_usd != null && <span className="text-faint">*</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        )}
        <p className="mt-3 text-[11.5px] text-faint">
          * Estimated. Codex is priced from OpenRouter's OpenAI prices; Claude uses the SDK's own estimate.
          Subscription use is covered by the plan and shown at API prices for comparison.
          {usage?.first_record ? ` Recording since ${new Date(usage.first_record).toLocaleDateString()}.` : ''}
        </p>
      </section>
    </div>
  )
}
