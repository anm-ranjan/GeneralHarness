import { statusInfo } from '../../agentUi'

const TONES = {
  ok: 'bg-ok-soft text-ok',
  danger: 'bg-danger-soft text-danger',
  warn: 'bg-warn-soft text-warn',
  info: 'bg-info-soft text-info',
  faint: 'bg-surface text-faint',
}

export default function StatusPill({ status, label, className = '' }) {
  const info = statusInfo(status)
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4 ${TONES[info.tone]} ${className}`}>
      {['running', 'starting', 'waiting_approval', 'waiting_input'].includes(status) && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" aria-hidden="true" />}
      {label || info.label}
    </span>
  )
}
