import { useApp } from '../../context/AppContext'

export default function ConfirmDialog() {
  const { state, dispatch } = useApp()
  const dialog = state.confirmDialog
  if (!dialog) return null

  function close() {
    dispatch({ type: 'CLOSE_CONFIRM' })
  }

  function confirm() {
    close()
    dialog.onConfirm?.()
  }

  const dangerous = dialog.tone === 'danger'

  return (
    <div className="fixed inset-0 z-50 glass-overlay flex items-center justify-center p-5">
      <section className="w-full max-w-[420px] rounded-lg border border-line bg-bg shadow-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-line">
          <h2 className="text-[15px] font-semibold text-text-bright">{dialog.title || 'Confirm action'}</h2>
          {dialog.message && (
            <p className="mt-1.5 text-[13px] leading-5 text-muted">{dialog.message}</p>
          )}
        </div>
        {dialog.detail && (
          <div className="px-5 py-3 border-b border-line bg-black/20">
            <pre className="text-[12px] leading-5 font-mono text-faint whitespace-pre-wrap max-h-40 overflow-y-auto">{dialog.detail}</pre>
          </div>
        )}
        <div className="flex items-center justify-end gap-2 px-5 py-3">
          <button
            type="button"
            onClick={close}
            className="px-3 py-1.5 text-[13px] font-medium text-muted border border-line rounded-md hover:text-text-bright hover:border-line-hover transition-colors"
          >
            {dialog.cancelLabel || 'Cancel'}
          </button>
          <button
            type="button"
            onClick={confirm}
            className={`px-3 py-1.5 text-[13px] font-semibold rounded-md transition-colors ${
              dangerous
                ? 'bg-danger text-white hover:brightness-110'
                : 'bg-accent text-bg hover:brightness-110'
            }`}
          >
            {dialog.confirmLabel || 'Confirm'}
          </button>
        </div>
      </section>
    </div>
  )
}
