// The thread being dragged in the sidebar. Drag data cannot be read until the
// drop, but a project must know during dragover whether the thread is its own
// (reorder) or another project's (move), to show the right drop marker.
let dragged = null

export function startSessionDrag(sessionId, projectId) {
  dragged = { sessionId, projectId }
}

export function endSessionDrag() {
  dragged = null
}

export function draggedSession() {
  return dragged
}
