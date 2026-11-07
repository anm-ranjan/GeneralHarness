export function foldAgentRun(items, eventType, data, metadata = {}) {
  const existing = items.find(i => i.type === 'agent_run_status' && i.toolCallId === data.tool_call_id)
  const item = {...(existing || {type:'agent_run_status',_id:'agent_script_'+data.tool_call_id,toolCallId:data.tool_call_id,items:{},...metadata})}
  if (eventType === 'agent_run_item') item.items = {...item.items,[data.key]:data}
  if (eventType === 'agent_run_progress') item.progress = data
  if (eventType === 'agent_run_result') {
    item.result = data
    if (data.items_snapshot) item.items = Object.fromEntries(data.items_snapshot.map(i=>[i.key,i]))
  }
  return existing ? items.map(i=>i===existing ? item : i) : [...items,item]
}
export function agentInvocation(text, profiles) {
  const match = /^@([a-z0-9][a-z0-9-]*)(?:\s+([\s\S]*))?$/.exec(text.trim())
  const profile = match && profiles.find(p=>p.name===match[1])
  return profile ? {name:profile.name,text:match[2] || ''} : null
}

// An invocation can cross the transcript paging boundary. Keep its earliest
// position, then apply the newer page's upserts/result over the older page.
export function mergeRoutineStageItems(items) {
  const result = [], positions = new Map()
  for (const item of items) {
    if (item.type !== 'agent_run_status') {result.push(item);continue}
    const index = positions.get(item.toolCallId)
    if (index === undefined) {positions.set(item.toolCallId,result.length);result.push(item);continue}
    const old = result[index]
    result[index] = {...old,...item,items:{...old.items,...item.items},progress:item.progress || old.progress,result:item.result || old.result,eventIndex:old.eventIndex}
  }
  return result
}
