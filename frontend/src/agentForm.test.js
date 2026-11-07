import test from 'node:test'
import assert from 'node:assert/strict'
import { initialValues, toRequest, validate } from './agentForm.js'
import { initialState, reducer } from './context/appReducer.js'
import { handleSessionEvent } from './eventHandlers.js'
const params = { paths:{type:'paths',required:true}, stagger:{type:'float',default:2,min:0,max:60}, mode:{type:'enum',options:['one','two']}, flag:{type:'bool'} }
test('agent form defaults, conversion and errors',()=>{
  assert.deepEqual(initialValues(params),{paths:'',stagger:2,mode:'',flag:false})
  assert.deepEqual(toRequest(params,{paths:' /one\n/two ',stagger:'2.5'}),{paths:['/one','/two'],stagger:2.5})
  assert.deepEqual(validate(params,{paths:'/one',stagger:'0'}),[])
  assert.equal(validate(params,{paths:'',stagger:'-1',mode:'bad'}).length,3)
})
test('agent events fold identically live and replay, upserting items',()=>{
  const events=[{type:'agent_run_progress',data:{tool_call_id:'x',done:1,total:2,label:'Starting'}},{type:'agent_run_item',data:{tool_call_id:'x',key:'a',status:'running'}},{type:'agent_run_item',data:{tool_call_id:'x',key:'a',status:'ok'}},{type:'agent_run_result',data:{tool_call_id:'x',status:'completed',summary:'Done'}}]
  const fold=()=>{let state={...initialState,stageItems:[]};for(const e of events)handleSessionEvent(e,a=>{state=reducer(state,a)},{current:state});return state}
  const state=fold();assert.equal(state.stageItems.length,1);assert.equal(state.stageItems[0].items.a.status,'ok');assert.equal(state.isRunning,false)
  assert.deepEqual(fold().stageItems,state.stageItems)
})

import { agentInvocation } from './agentRunState.js'
import { isAgentRunSession, activitySessions } from './sessionLabels.js'
test('agent invocation recognizes known names and leaves unknown mentions alone',()=>{
  const profiles=[{name:'job-status',errors:[]},{name:'unavailable',errors:[],ready:false}]
  assert.deepEqual(agentInvocation('@job-status show running jobs',profiles),{name:'job-status',text:'show running jobs'})
  assert.equal(agentInvocation('@someone ordinary text',profiles),null)
  assert.equal(agentInvocation('mention @job-status later',profiles),null)
  assert.deepEqual(agentInvocation('@unavailable check',profiles),{name:'unavailable',text:'check'})
})
test('agent runs remain in activity and are structurally identified',()=>{
  const run={id:'run',kind:'agent_run',project_id:'__agents__',archived:false}
  assert.equal(isAgentRunSession(run),true)
  assert.equal(isAgentRunSession({kind:'project',labels:['agent_run']}),false)
  assert.deepEqual(activitySessions({run}),[run])
})
test('idle session replay folds routine events and drafts without live indicators',()=>{
  let state={...initialState,currentSessionId:'run',stageItems:[]}
  const dispatch=a=>{state=reducer(state,a)}
  handleSessionEvent({session_id:'run',type:'session_loaded',data:{meta:{id:'run',status:'idle',kind:'agent_run',provider:'native'},events:[
    {type:'user_message',data:{text:'start'}},
    {type:'agent_run_item',data:{tool_call_id:'call',key:'a',status:'ok'}},
    {type:'agent_run_result',data:{tool_call_id:'call',status:'completed',summary:'done'}},
    {type:'run_finished',data:{reason:'completed'}},
  ]}},dispatch,{current:state})
  assert.equal(state.isRunning,false)
  const folded=state.stageItems.filter(i=>i.type==='agent_run_status')
  assert.equal(folded.length,1);assert.equal(folded[0].result.summary,'done');assert.equal(folded[0].items.a.status,'ok')
  assert.equal(state.stageItems.some(i=>['indicator','thinking_stream'].includes(i.type)),false)
})

import { foldAgentRun, mergeRoutineStageItems } from './agentRunState.js'
test('final snapshots restore older items and paging keeps one invocation',()=>{
  const older=foldAgentRun([],'agent_run_item',{tool_call_id:'x',key:'a',status:'running'})
  let newer=foldAgentRun([],'agent_run_result',{tool_call_id:'x',status:'completed',summary:'done',items_snapshot:[{key:'a',status:'ok'},{key:'b',status:'failed'}]})
  const combined=mergeRoutineStageItems([...older,...newer])
  assert.equal(combined.length,1);assert.equal(combined[0].items.a.status,'ok');assert.equal(combined[0].items.b.status,'failed')
  assert.equal(combined[0].result.summary,'done')
})
