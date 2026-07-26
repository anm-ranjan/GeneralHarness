import { completePalette } from '../../theme'

/** A miniature of the app painted in a palette's own colors, so the grid shows
 *  what each theme looks like without having to apply it. */
export default function ThemeSwatch({ palette, selected, accentOverride, onSelect, onDelete }) {
  const p = completePalette(palette)
  const accent = accentOverride || p.accent

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => onSelect(palette.id)}
        title={p.name}
        className={`w-full text-left rounded-md overflow-hidden border transition-all ${
          selected
            ? 'border-accent ring-1 ring-accent'
            : 'border-line hover:border-line-hover'
        }`}
        style={{ background: p.bg }}
      >
        <div className="flex gap-1 px-2 py-1.5" style={{ background: p.panel }}>
          <span className="h-2 w-2 rounded-full" style={{ background: accent }} />
          <span className="h-2 w-2 rounded-full" style={{ background: p.ok }} />
          <span className="h-2 w-2 rounded-full" style={{ background: p.danger }} />
        </div>
        <div className="px-2 py-2 space-y-1">
          <div className="h-1.5 rounded-full w-3/4" style={{ background: p.textBright }} />
          <div className="h-1.5 rounded-full w-1/2" style={{ background: p.muted }} />
          <div className="flex gap-1 pt-0.5">
            <span className="h-1.5 w-4 rounded-full" style={{ background: p.syntax.keyword }} />
            <span className="h-1.5 w-6 rounded-full" style={{ background: p.syntax.string }} />
            <span className="h-1.5 w-3 rounded-full" style={{ background: p.syntax.number }} />
          </div>
        </div>
        <div
          className="px-2 py-1 text-[11px] truncate border-t"
          style={{ color: p.text, borderColor: p.line || 'transparent', background: p.surface }}
        >
          {p.name}
        </div>
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={() => onDelete(palette.id)}
          title="Delete this saved theme"
          className="absolute -top-1.5 -right-1.5 h-5 w-5 rounded-full bg-surface-raised border border-line text-muted text-[11px] leading-none hover:text-danger hover:border-danger/40"
        >
          ×
        </button>
      )}
    </div>
  )
}
