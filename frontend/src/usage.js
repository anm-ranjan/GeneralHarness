// Formatting and totals for the Usage settings tab.

const PROVIDER_LABELS = { native: 'Native', 'codex-app-server': 'Codex', 'claude-agent': 'Claude' }

export function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider || 'Unknown'
}

export function formatTokens(n) {
  const value = Number(n) || 0
  if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e10 ? 0 : 1)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(value >= 1e7 ? 0 : 1)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(value >= 1e4 ? 0 : 1)}k`
  return String(Math.round(value))
}

export function formatCost(usd) {
  const value = Number(usd) || 0
  if (value === 0) return '$0.00'
  if (value < 0.01) return `$${value.toFixed(4)}`
  if (value < 1) return `$${value.toFixed(3)}`
  if (value < 100) return `$${value.toFixed(2)}`
  return `$${Math.round(value).toLocaleString()}`
}

/** "in 3h 12m" for a unix-seconds time today, otherwise a short date. */
export function formatReset(unixSeconds, now = Date.now()) {
  const ms = Number(unixSeconds) * 1000
  if (!ms) return ''
  const diff = ms - now
  if (diff <= 0) return 'now'
  const minutes = Math.round(diff / 60000)
  if (minutes < 60) return `in ${minutes}m`
  if (minutes < 24 * 60) return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`
  return new Date(ms).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}

export function costKindNote(row) {
  if (row.cost_usd == null) return 'No price known for this model'
  if (row.cost_kind === 'reported') return 'Charged by OpenRouter'
  if (row.cost_kind === 'equivalent') return 'Covered by your subscription; API-price equivalent'
  return 'Estimated'
}

/** Charged (API) and plan-covered (subscription) dollars, and all tokens. */
export function usageTotals(rows) {
  let charged = 0
  let equivalent = 0
  let tokens = 0
  for (const row of rows || []) {
    tokens += (row.input_tokens || 0) + (row.output_tokens || 0)
    if (row.cost_usd == null) continue
    if (row.cost_kind === 'equivalent') equivalent += row.cost_usd
    else charged += row.cost_usd
  }
  return { charged, equivalent, tokens }
}
