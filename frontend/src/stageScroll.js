// Whether the transcript should follow new output or leave the reader where
// they are and offer "Show new messages" instead.

export const NEAR_BOTTOM_PX = 80

export function isNearBottom({ scrollHeight, scrollTop, clientHeight }, threshold = NEAR_BOTTOM_PX) {
  return scrollHeight - scrollTop - clientHeight <= threshold
}

// 'bottom': jump to the newest output. 'notify': keep the position and show
// the button. Opening a session (or replaying its events) and sending your own
// message always go to the bottom; otherwise follow only a reader already there.
export function followNewOutput({ sessionChanged, replaying, ownMessage, atBottom }) {
  if (sessionChanged || replaying || ownMessage || atBottom) return 'bottom'
  return 'notify'
}
