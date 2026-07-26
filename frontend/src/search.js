// Pure helpers for the global transcript search UI, kept out of the React component so
// they can be unit-tested with node:test like the other frontend modules.

export const SEARCH_FIELD_LABELS = {
  prompt: 'Prompt',
  response: 'Response',
  tool: 'Tool',
  tool_result: 'Tool result',
  file: 'File',
}

export function fieldLabel(field) {
  return SEARCH_FIELD_LABELS[field] || field
}

// Finds the stage item that best anchors an absolute event index for search
// deep-linking: the closest item at or before the target (stage items are a
// filtered projection of the event log, so the exact index may be absent),
// falling back to the first item after it. Returns null when nothing anchors.
export function findAnchorEventIndex(stageItems, targetIndex) {
  let before = null
  let after = null
  for (const item of stageItems || []) {
    if (item.eventIndex == null) continue
    if (item.eventIndex <= targetIndex) {
      if (before === null || item.eventIndex > before) before = item.eventIndex
    } else if (after === null || item.eventIndex < after) {
      after = item.eventIndex
    }
  }
  return before ?? after
}

// Group a flat list of search hits by session, preserving the order in which each
// session first appears (the backend already returns sessions most-recent first).
export function groupHitsBySession(hits) {
  const groups = []
  const index = new Map()
  for (const hit of hits || []) {
    let group = index.get(hit.session_id)
    if (!group) {
      group = { session: hit, items: [] }
      index.set(hit.session_id, group)
      groups.push(group)
    }
    group.items.push(hit)
  }
  return groups
}
