export default function ProviderLogo({ provider, className = 'h-4 w-4' }) {
  if (provider === 'claude-agent') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M12 2.5v19M4.1 7l15.8 10M4.1 17 19.9 7M2.5 12h19" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" />
      </svg>
    )
  }

  if (provider === 'codex-app-server' || provider === 'codex-cli') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M12 3.1a4.4 4.4 0 0 1 7.5 3.1v2.2a4.4 4.4 0 0 1 0 7.5l-1.9 1.1a4.4 4.4 0 0 1-6.5 3.7l-1.9-1.1a4.4 4.4 0 0 1-6.5-3.7v-2.2a4.4 4.4 0 0 1 0-7.5l1.9-1.1A4.4 4.4 0 0 1 12 3.1Z" stroke="currentColor" strokeWidth="1.7" />
        <path d="m8.1 9.7 3.9-2.2 3.9 2.2v4.6L12 16.5l-3.9-2.2V9.7Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    )
  }

  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2.8 8 4.6v9.2l-8 4.6-8-4.6V7.4l8-4.6Z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8.5 9.5h7v5h-7z" fill="currentColor" opacity=".7" />
    </svg>
  )
}
