/** Copy from a user action, including on HTTP hosts without the Clipboard API. */
export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch {
    // Some browsers expose the API but deny writes. Try the selection fallback.
  }

  const focused = document.activeElement
  const selection = document.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange())
    : []
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    if (!document.execCommand('copy')) throw new Error('Clipboard copy was blocked')
  } finally {
    textarea.remove()
    focused?.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      for (const range of ranges) selection.addRange(range)
    }
  }
}
