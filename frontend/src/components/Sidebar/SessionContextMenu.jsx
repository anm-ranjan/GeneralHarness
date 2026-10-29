import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// Right-click menu for a sidebar session. `items` is a list of
// { label, onSelect, tone?, disabled? } or the string 'separator'.
export default function SessionContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null)
  const [pos, setPos] = useState({ left: x, top: y })

  // Keep the menu on screen near the window edges.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    setPos({
      left: Math.max(4, Math.min(x, window.innerWidth - width - 4)),
      top: Math.max(4, Math.min(y, window.innerHeight - height - 4)),
    })
    el.querySelector('button:not(:disabled)')?.focus()
  }, [x, y])

  useEffect(() => {
    function onPointer(e) { if (!ref.current?.contains(e.target)) onClose() }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      e.preventDefault()
      const buttons = [...(ref.current?.querySelectorAll('button:not(:disabled)') || [])]
      const index = buttons.indexOf(document.activeElement)
      const next = e.key === 'ArrowDown' ? index + 1 : index - 1
      buttons[(next + buttons.length) % buttons.length]?.focus()
    }
    window.addEventListener('mousedown', onPointer, true)
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('blur', onClose)
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('mousedown', onPointer, true)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('blur', onClose)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return (
    <div
      ref={ref}
      role="menu"
      className="fixed z-50 min-w-[190px] py-1 rounded-md border border-line bg-bg shadow-2xl text-[13px]"
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={e => e.preventDefault()}
    >
      {items.map((item, i) => item === 'separator' ? (
        <div key={`sep_${i}`} className="my-1 border-t border-line" role="separator" />
      ) : (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          title={item.title}
          onClick={() => { onClose(); item.onSelect() }}
          className={`block w-full text-left px-3 py-1.5 focus:outline-none disabled:opacity-40 ${
            item.tone === 'danger'
              ? 'text-danger hover:bg-danger-soft focus:bg-danger-soft'
              : 'text-text-default hover:bg-surface-hover hover:text-accent focus:bg-surface-hover focus:text-accent'
          }`}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}
