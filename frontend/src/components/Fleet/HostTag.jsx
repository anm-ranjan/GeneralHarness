import { HOST_TAGS } from '../../fleet'

const TONE = {
  connecting: 'text-faint',
  online: 'text-info',
  offline: 'text-danger',
  serving: 'text-ok',
  notServing: 'text-warn',
}

/** The single status word for a machine; a pulse marks "still finding out". */
export default function HostTag({ tag, busy }) {
  const text = busy === 'start' ? 'Starting…' : busy === 'stop' ? 'Stopping…' : HOST_TAGS[tag]
  const pulsing = tag === 'connecting' || busy
  return (
    <span className={`inline-flex items-center gap-1.5 ${busy ? 'text-faint' : TONE[tag]}`}>
      <span className={`h-1.5 w-1.5 rounded-full bg-current ${pulsing ? 'animate-pulse' : ''}`} aria-hidden="true" />
      {text}
    </span>
  )
}
