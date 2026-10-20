const {connect} = require('./transport.cjs');
const {fault,remoteFault} = require('./diagnostics.cjs');
const {preflight,describe} = require('./compact.cjs');
const fs = require('node:fs');
const {randomUUID} = require('node:crypto');
const ORIGIN_THREAD = process.env.HERMES_CODEX_CALLER_THREAD_ID || '';
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function selectPipe(names) {
  if(!names){
    try{names=fs.readdirSync('\\\\.\\pipe\\');}
    catch(e){throw fault(e.code==='EACCES'||e.code==='EPERM'?'PERMISSION_DENIED':'DESKTOP_PIPE_MISSING','discover');}
  }
  const matches = names.filter(n => n.startsWith('codex-app-tools-') && UUID.test(n.slice(16)));
  if (!matches.length) throw fault('DESKTOP_PIPE_MISSING','discover');
  if (matches.length !== 1) throw fault('DESKTOP_PIPE_AMBIGUOUS','discover');
  return `\\\\.\\pipe\\${matches[0]}`;
}


function pick(obj, keys) {
  return Object.fromEntries(keys.filter(k => obj?.[k] !== undefined).map(k => [k, obj[k]]));
}
function redact(text) {
  // Mask before clipping so a secret crossing the output limit cannot leak a prefix.
  return text
    .replace(/-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)/g, '[REDACTED]')
    .replace(/\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b/g, '[REDACTED]')
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 [REDACTED]')
    .replace(/(\b(?:[A-Z][A-Z0-9]*_)*(?:API_?KEY|ACCESS_?TOKEN|REFRESH_?TOKEN|BOT_?TOKEN|CLIENT_?SECRET|PASSWORD|TOKEN|SECRET)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]]+)/gi, '$1[REDACTED]');
}
function publicMessages(items, messageLimit, maxChars) {
  return (items ?? []).filter(i => i.type === 'userMessage' ||
    (i.type === 'agentMessage' && (!i.phase || ['commentary','final','final_answer'].includes(i.phase))))
    .slice(-messageLimit).map(i => ({role:i.type === 'userMessage' ? 'user' : 'assistant',
      ...(i.phase ? {phase:i.phase} : {}),
      text:redact(i.text ?? (i.content ?? []).filter(c => c.type === 'text').map(c => c.text).join('\n')).slice(0,maxChars)}));
}

function relatedAgents(items) {
  const ids=new Set();
  for(const i of items??[]){
    if(/^collabAgent.*ToolCall$/.test(i.type)){
      for(const id of [i.newThreadId,i.receiverThreadId,...(i.receiverThreadIds??[]),...Object.keys(i.agentsStates??{})])
        if(typeof id==='string'&&UUID.test(id))ids.add(id);
    }
    if(i.type==='subAgentActivity'&&UUID.test(i.agentThreadId))ids.add(i.agentThreadId);
  }
  return [...ids].map(threadId=>({threadId}));
}
function parseAppJson(response,tool){
  let data;
  try{data=JSON.parse(response.contentItems?.find(i=>i.type==='inputText')?.text);}
  catch{throw fault('PROTOCOL_INVALID','parse',{tool,requestState:'received',deliveryState:'failed'});}
  if(!data||typeof data!=='object'||Array.isArray(data))throw fault('PROTOCOL_INVALID','parse',{tool});
  if(data?.error||data?.isError||data?.ok===false||data?.success===false)throw remoteFault(data,'read',{tool});
  return data;
}
async function appCall(client,catalog,tool,args,callId=`hermes-bridge-${randomUUID()}`,callerThreadId=ORIGIN_THREAD){
  if(!UUID.test(callerThreadId))throw Error('Invalid desktop routing context');
  if(!['list_threads','read_thread','send_message_to_thread'].includes(tool))throw Error('unsupported tool');
  if(!catalog?.tools?.some(t=>t.name===tool&&t.namespace==='codex_app'))throw fault('CAPABILITY_UNAVAILABLE','handshake',{tool});
  const r=await client.request('tools/call',{arguments:args,callerSource:'codex',hostId:'local',namespace:'codex_app',
    threadId:callerThreadId,turnId:`hermes-bridge-${randomUUID()}`,callId,tool});
  if(!r?.success)throw remoteFault(r,'request',{tool,requestId:callId});
  return r;
}

async function query(command, threadId = '', options = {}) {
  if (!['list','status','read'].includes(command)) throw Error('unsupported command: use list, status or read');
  if (command !== 'list' && !UUID.test(threadId)) throw Error('Invalid thread ID');
  const bounded=(key,fallback,min,max)=>{
    const value=options[key]===undefined?fallback:options[key];
    if(!Number.isInteger(value)||value<min||value>max)throw Error(`Invalid query option: ${key}`);
    return value;
  };
  const limit=command==='list'?bounded('limit',10,1,100):10;
  const offset=command==='list'?bounded('offset',0,0,1000):0;
  const turnLimit=command==='read'?bounded('turn_limit',2,1,10):2;
  const messageLimit=command==='read'?bounded('message_limit',12,1,20):0;
  const maxChars=command==='read'?bounded('max_chars',1000,1,2000):0;
  if(command==='list'&&options.compact!==undefined&&typeof options.compact!=='boolean')throw Error('Invalid query option: compact');
  if(options.cursor!==undefined&&(command!=='read'||typeof options.cursor!=='string'||!options.cursor.length||options.cursor.length>8192))
    throw Error('Invalid query option: cursor');
  const tool = command === 'list' ? 'list_threads' : 'read_thread';
  const args = command === 'list' ? {limit:limit+offset} : {
    threadId, hostId:'local', turnLimit,
    ...(options.cursor!==undefined?{cursor:options.cursor}:{}),
    includeOutputs:false, maxOutputCharsPerItem:maxChars
  };
  let client;
  try {
    client = connect(options.pipe ?? selectPipe(), options.timeoutMs ?? 15000);
    const catalog = await client.request('tools/list', {threadStartKind:'all'});
    const data=parseAppJson(await appCall(client,catalog,tool,args,undefined,options.callerThreadId),tool);
    const base = {source:'codex-desktop-live', observedAt:new Date().toISOString(), hostId:'local'};
    if (command === 'list') {
      if (!Array.isArray(data.threads) || !Array.isArray(data.pinnedThreads)) throw fault('PROTOCOL_INVALID','parse',{tool,requestState:'received',deliveryState:'failed'});
      const rows=[...data.pinnedThreads,...data.threads].filter(t => t.kind === 'codex' || !t.kind);
      const end=offset+limit;
      // Native list has only a limit; offsets re-fetch a changing pinned-first snapshot.
      const exhausted=data.threads.length<limit+offset;
      return {...base, threads:rows.slice(offset,end).map(t=>{
        const row=pick(t,['id','title','status','hostId','cwd','updatedAt']);
        if(options.compact!==false&&typeof row.title==='string'&&row.title.length>120){
          row.title_preview=row.title.slice(0,120);delete row.title;
          row.full_title_required='codex_status/codex_read';
        }
        return row;
      }),pagination:{mode:'local-offset',limit,offset,returned:rows.slice(offset,end).length,
        nextOffset:rows.length>end||!exhausted?end:null,
        hasMore:rows.length>end?true:exhausted?false:null,nativeCursorSupported:false},
        unavailableHosts:data.unavailableHosts ?? [], unavailableSources:data.unavailableSources ?? []};
    }
    if (data.thread?.id !== threadId || typeof data.thread?.status?.type !== 'string')
      throw fault('THREAD_NOT_FOUND','lookup',{tool,requestState:'received',deliveryState:'failed'});
    const thread = pick(data.thread,['id','title','status','hostId','cwd','updatedAt']);
    const turns = (data.turns ?? []).slice(0,turnLimit).map(t => ({...pick(t,['id','status','startedAt','completedAt']),
      ...(command === 'read' ? {messages:publicMessages(t.items,messageLimit,maxChars),subagents:relatedAgents(t.items)} : {})}));
    return {...base, thread, compactState:describe(data), recentTurnIds:(data.turns??[]).slice(0,2).map(t=>t.id), latestTurn:turns[0]?pick(turns[0],['id','status','startedAt','completedAt']):null,
      ...(command === 'read' ? {turns, page:data.page} : {})};
  } catch(e){
    e.tool ??= tool;e.source ??= 'codex-desktop-live';e.observedAt ??= new Date().toISOString();e.requestId ??= `hermes-query-${randomUUID()}`;
    e.automaticRetrySafe=true;throw e;
  } finally {client?.close();}
}

function operationError(error,dispatched,requestId){
  error.deliveryState=dispatched?(error.requestState==='rejected'?'failed':'unknown'):'not-sent';
  error.automaticRetrySafe=false;error.requestId=requestId;
  return error;
}
function validateTarget(data,id,expectedTitle){
  if(data?.thread?.id!==id||data.thread.kind!=='codex'||typeof data.thread.status?.type!=='string')
    throw Object.assign(Error('無法查證指定目標：回傳的 ID、對話種類或狀態不符合本機 Codex 對話。操作未派送，請用 codex_list 重新核對，不要自動重送。'),
      {code:'TARGET_LOOKUP_FAILED',threadId:id});
  if(expectedTitle!==undefined&&data.thread.title!==expectedTitle)
    throw Object.assign(Error('目標名稱與 ID 不符；請重新 codex_list 定位，再以 codex_status/read 取得完整名稱與 ID，不要猜測或自動改用其他對話。'),
      {code:'TARGET_TITLE_MISMATCH',threadId:id});
}
async function readTarget(client,catalog,threadId,options){
  if(options.expectedTitle!==undefined&&(typeof options.expectedTitle!=='string'||!options.expectedTitle.trim()))
    throw Error('Invalid expected target title');
  let data;
  try{
    data=parseAppJson(await appCall(client,catalog,'read_thread',
      {threadId,hostId:'local',turnLimit:1,includeOutputs:false,maxOutputCharsPerItem:0},undefined,options.callerThreadId));
  }catch(e){
    e.threadId=threadId;throw e;
  }
  validateTarget(data,threadId,options.expectedTitle);
  return data;
}
async function send(threadId,prompt,options={}){
  if(!UUID.test(threadId))throw Error('Invalid thread ID');
  if(typeof prompt!=='string'||!prompt.trim()||Buffer.byteLength(prompt,'utf8')>65536)throw Error('Invalid or oversized message');
  let client,dispatched=false;const requestId=`hermes-send-${randomUUID()}`;
  try{
    client=connect(options.pipe??selectPipe(),options.timeoutMs??15000);
    const catalog=await client.request('tools/list',{threadStartKind:'all'});
    if(!catalog?.tools?.some(t=>t.name==='send_message_to_thread'&&t.namespace==='codex_app'))throw Error('Send capability unavailable');
    const before=await readTarget(client,catalog,threadId,options);
    if(options.expectedBaselineTurnId&&before.turns?.[0]?.id!==options.expectedBaselineTurnId)
      throw fault('TARGET_CHANGED','lookup');
    if(options.expectedBaselineTurnId&&before.thread.status.type!=='idle')throw fault('TARGET_CHANGED','lookup');
    const idle=preflight(before);
    if(idle.required&&!options.skipCompaction)throw fault('COMPACT_CONFIRMATION_REQUIRED','compact');
    dispatched=true;
    const r=await appCall(client,catalog,'send_message_to_thread',{threadId,hostId:'local',prompt},requestId,options.callerThreadId);
    return {source:'codex-desktop-live',observedAt:new Date().toISOString(),threadId,requestId,
      deliveryState:'accepted',executionComplete:false,previousStatus:before.thread.status,
      targetTitle:before.thread.title,
      turnBaseline:before.turns?.[0]?pick(before.turns[0],['id','status','startedAt','completedAt']):null,
      followUp:{tool:'codex_status',thread_id:threadId,suggestedIntervalSeconds:30},
      receipt:(r.contentItems??[]).filter(i=>i.type==='inputText').map(i=>redact(i.text).slice(0,1000))};
  }catch(e){throw operationError(e,dispatched,requestId);}
  finally{client?.close();}
}
async function stop(threadId,options={}){
  if(!UUID.test(threadId))throw Error('Invalid thread ID');
  let client,dispatched=false;const requestId=`hermes-stop-${randomUUID()}`;
  try{
    const app=connect(options.pipe??selectPipe(),options.timeoutMs??15000);
    let before;
    try{
      const catalog=await app.request('tools/list',{threadStartKind:'all'});
      before=await readTarget(app,catalog,threadId,options);
    }finally{app.close();}
    if(before.thread.status.type==='notLoaded')return {source:'codex-desktop-live',observedAt:new Date().toISOString(),threadId,requestId,targetTitle:before.thread.title,deliveryState:'not-active',stopConfirmed:true,previousStatus:before.thread.status};
    client=connect(options.ipcPipe??'\\\\.\\pipe\\codex-ipc',options.timeoutMs??15000,true);
    const init=await client.request('initialize',{clientType:'hermes-session-control'},{sourceClientId:'initializing-client',version:0});
    const sourceClientId=init.result?.clientId;
    if(!UUID.test(sourceClientId))throw Error('Invalid Codex control initialization');
    const owner=await client.request('thread-owner-discovery',{hostId:'local',conversationId:threadId},{sourceClientId,version:1});
    if(!UUID.test(owner.handledByClientId))throw Error('Codex thread owner unavailable');
    dispatched=true;
    // Version 3 user-stop includes native Goal pause/background cleanup; v4 with an expected turn skips Goal pause.
    const response=await client.request('thread-follower-interrupt-turn',{conversationId:threadId,mode:'user-stop'},
      {sourceClientId,version:3,targetClientId:owner.handledByClientId});
    const result=response.result;
    if(response.handledByClientId!==owner.handledByClientId||result?.ok!==true)throw Error('Codex owner did not acknowledge interruption');
    client.close();client=null;
    let after=null;
    try{after=await query('status',threadId,options);}catch{}
    return {source:'codex-desktop-live',observedAt:new Date().toISOString(),threadId,requestId,
      targetTitle:before.thread.title,
      deliveryState:result.goalPauseError?'partial':'accepted',interruptedTurnId:result.interruptedTurnId??null,
      goalPauseFailed:!!result.goalPauseError,stopConfirmed:!result.goalPauseError&&!!after&&['idle','notLoaded'].includes(after.thread.status.type),
      status:after?.thread.status??null,latestTurn:after?.latestTurn??null};
  }catch(e){throw operationError(e,dispatched,requestId);}
  finally{client?.close();}
}
async function compactStart(threadId,options={}){
  if(!UUID.test(threadId)||typeof options.expectedBaselineTurnId!=='string'||!options.expectedBaselineTurnId)
    throw fault('TARGET_CHANGED','validate');
  let app,ipc,dispatched=false;const requestId=`hermes-compact-${randomUUID()}`;
  const check=data=>{
    if(data.thread.status.type!=='idle'||data.turns?.[0]?.id!==options.expectedBaselineTurnId)
      throw fault('TARGET_CHANGED','lookup');
    preflight(data);
  };
  try{
    app=connect(options.pipe??selectPipe(),options.timeoutMs??15000);
    const catalog=await app.request('tools/list',{threadStartKind:'all'});
    check(await readTarget(app,catalog,threadId,options));
    ipc=connect(options.ipcPipe??'\\\\.\\pipe\\codex-ipc',options.timeoutMs??15000,true);
    const init=await ipc.request('initialize',{clientType:'hermes-session-control'},{sourceClientId:'initializing-client',version:0});
    const sourceClientId=init.result?.clientId;
    if(!UUID.test(sourceClientId))throw fault('PROTOCOL_INVALID','handshake');
    const owner=await ipc.request('thread-owner-discovery',{hostId:'local',conversationId:threadId},{sourceClientId,version:1});
    if(!UUID.test(owner.handledByClientId))throw fault('CAPABILITY_UNAVAILABLE','lookup');
    check(await readTarget(app,catalog,threadId,options));
    dispatched=true;
    const r=await ipc.request('thread-follower-compact-thread',{conversationId:threadId},
      {sourceClientId,version:1,targetClientId:owner.handledByClientId});
    if(r.handledByClientId!==owner.handledByClientId||r.result?.ok!==true)throw fault('COMPACT_UNCONFIRMED','compact');
    return {deliveryState:'accepted',compactComplete:false,requestId,threadId,baselineTurnId:options.expectedBaselineTurnId,
      automaticRetrySafe:false,observedAt:new Date().toISOString()};
  }catch(e){throw operationError(e,dispatched,requestId);}
  finally{app?.close();ipc?.close();}
}
module.exports = {query, selectPipe, send, stop,compactStart};
