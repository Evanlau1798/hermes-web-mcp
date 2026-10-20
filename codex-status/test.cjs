process.env.HERMES_CODEX_CALLER_THREAD_ID = '11111111-1111-4111-8111-111111111111';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const {randomUUID} = require('node:crypto');
const {query, selectPipe, send, stop} = require('./bridge.cjs');
const ID = '11111111-1111-4111-8111-111111111111';
const OWNER = '33333333-3333-4333-8333-333333333333';

async function fixture(t, handler) {
  const pipe = process.platform === 'win32' ? `\\\\.\\pipe\\hermes-test-${randomUUID()}` : `/tmp/hermes-${randomUUID()}.sock`;
  const sockets = new Set();
  const server = net.createServer(socket => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket));
    let buf = Buffer.alloc(0);
    socket.on('data', chunk => {
      buf = Buffer.concat([buf, chunk]);
      while (buf.length >= 4) {
        const n = buf.readUInt32LE();
        if (buf.length < n + 4) return;
        const req = JSON.parse(buf.subarray(4, n + 4)); buf = buf.subarray(n + 4);
        const result = handler(req, socket);
        if (result === undefined) continue;
        const reply=req.type==='request' ? {type:'response',requestId:req.requestId,method:req.method,resultType:'success',handledByClientId:req.method==='initialize'?ID:OWNER,result} : {id:req.id,jsonrpc:'2.0',result};
        const b = Buffer.from(JSON.stringify(reply));
        const h = Buffer.alloc(4); h.writeUInt32LE(b.length);
        socket.write(h.subarray(0, 2));
        setImmediate(() => socket.write(Buffer.concat([h.subarray(2), b])));
      }
    });
  });
  await new Promise(resolve => server.listen(pipe, resolve));
  t.after(async () => {for (const s of sockets) s.destroy(); await new Promise(r => server.close(r));});
  return pipe;
}
function response(data) {return {success:true, contentItems:[{type:'inputText', text:JSON.stringify(data)}]};}
const catalog = {tools:['list_threads','read_thread','wait_threads','send_message_to_thread'].map(name => ({name, namespace:'codex_app'}))};

test('synthetic routing context is forwarded through fixture pipes without turn-start calls', async t => {
  const caller='22222222-2222-4222-8222-222222222222';
  const methods=[];
  const pipe=await fixture(t, req=>{
    methods.push(req.method);
    if(req.method==='tools/list')return catalog;
    assert.equal(req.params.threadId,caller);
    assert.equal(req.params.tool,'read_thread');
    return response({thread:{id:ID,status:{type:'notLoaded'}},turns:[]});
  });
  const r=await query('status',ID,{pipe,callerThreadId:caller});
  assert.equal(r.thread.status.type,'notLoaded');
  assert.deepEqual(methods,['tools/list','tools/call']);
});

test('pipe discovery fails closed for missing or ambiguous desktop instances', () => {
  assert.throws(() => selectPipe([]), e=>e.code==='DESKTOP_PIPE_MISSING');
  const p = `codex-app-tools-${randomUUID()}`;
  assert.equal(selectPipe(['codex-ipc',p]), `\\\\.\\pipe\\${p}`);
  assert.throws(() => selectPipe([p,`codex-app-tools-${randomUUID()}`]), e=>e.code==='DESKTOP_PIPE_AMBIGUOUS');
});
test('status uses live read_thread, not disk timestamps; filters private and tool content', async t => {
  const calls=[];
  const pipe=await fixture(t, req => {
    calls.push(req);
    if(req.method==='tools/list') return catalog;
    assert.equal(req.params.tool,'read_thread');
    assert.equal(req.params.arguments.includeOutputs,false);
    return response({thread:{id:ID,title:'測試',status:{type:'active',activeFlags:['waitingOnUserInput']},preview:'PRIVATE'},
      turns:[{id:'turn1',status:'inProgress',items:[{type:'reasoning',raw_content:'SECRET'}]}]});
  });
  const result=await query('status',ID,{pipe,timeoutMs:1000});
  assert.equal(result.thread.status.type,'active');
  assert.deepEqual(result.thread.status.activeFlags,['waitingOnUserInput']);
  assert.equal(result.latestTurn.status,'inProgress');
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
  assert.ok(!JSON.stringify(result).includes('SECRET'));
  assert.deepEqual(calls.map(r=>r.method),['tools/list','tools/call']);
});
test('read emits only public messages, bounded in size', async t => {
  const pipe=await fixture(t, req => req.method==='tools/list' ? catalog : response({thread:{id:ID,status:{type:'idle'}},turns:[{id:'t',status:'completed',items:[
    {type:'reasoning',summary:['SECRET']}, {type:'commandExecution',command:'TOKEN'},
    {type:'userMessage',content:[{type:'text',text:'哈囉'}]},
    {type:'agentMessage',phase:'final',text:'a'.repeat(2000)},
    {type:'agentMessage',phase:'analysis',text:'HIDDEN'}]}]}));
  const r=await query('read',ID,{pipe,timeoutMs:1000});
  assert.equal(r.turns[0].messages.length,2);
  assert.equal(r.turns[0].messages[0].text,'哈囉');
  assert.equal(r.turns[0].messages[1].text.length,1000);
  assert.ok(!/SECRET|TOKEN|HIDDEN/.test(JSON.stringify(r)));
});
test('mutations, invalid IDs, and unsupported waits rejected before connecting', async () => {
  await assert.rejects(query('turn/start',ID), /unsupported/);
  await assert.rejects(query('status','bad'), /thread ID/);
  await assert.rejects(query('wait',ID), /unsupported/);
});
test('list excludes summaries and flags unavailable hosts', async t => {
  const pipe=await fixture(t, req => req.method==='tools/list' ? catalog : response({threads:[{id:ID,title:'原始標題',status:'active',summary:'PRIVATE'}],pinnedThreads:[],unavailableHosts:['local']}));
  const r=await query('list','',{pipe,timeoutMs:1000});
  assert.equal(r.threads[0].title,'原始標題');
  assert.deepEqual(r.unavailableHosts,['local']);
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
});
test('missing capability, timeout, EOF, and malformed frame fail without stale success', async t => {
  const pipe=await fixture(t, req=>req.method==='tools/list'?{tools:[]}:undefined);
  await assert.rejects(query('status',ID,{pipe,timeoutMs:500}), e=>e.code==='CAPABILITY_UNAVAILABLE');
  const silent=await fixture(t,()=>undefined);
  await assert.rejects(query('status',ID,{pipe:silent,timeoutMs:50}), e=>e.code==='DESKTOP_TIMEOUT');
  const closed=await fixture(t,(_r,s)=>{s.end();});
  await assert.rejects(query('status',ID,{pipe:closed,timeoutMs:500}), e=>e.code==='CONNECTION_CLOSED');
  const oversized=await fixture(t,(_r,s)=>{const h=Buffer.alloc(4);h.writeUInt32LE(9*1024*1024);s.write(h);});
  await assert.rejects(query('status',ID,{pipe:oversized,timeoutMs:500}), e=>e.code==='PROTOCOL_INVALID');
});
test('failed, malformed, and mismatched replies cannot become successful status', async t => {
  for (const result of [
    {success:false,contentItems:[]},
    {success:true,contentItems:[{type:'inputText',text:'not-json'}]},
    response({thread:{id:randomUUID(),status:{type:'active'}}}),
    response({thread:{id:ID,status:{}}})
  ]) {
    const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:result);
    await assert.rejects(query('status',ID,{pipe,timeoutMs:500}));
  }
});
test('read masks common credentials before truncating public text', async t => {
  const secrets=[
    'sk-'+ 'a'.repeat(32), 'github_pat_'+ 'b'.repeat(40),
    'c'.repeat(24)+'.'+'d'.repeat(6)+'.'+'e'.repeat(30),
    'Bearer fixture-access-token', 'DEEPSEEK_API_KEY="fixture-secret"',
    'password: fixture-password', '-----BEGIN PRIVATE KEY-----\nfixture-body\n-----END PRIVATE KEY-----'
  ];
  const text=secrets.join('\n')+'\n繁體中文 and English';
  const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:response({thread:{id:ID,status:{type:'idle'}},turns:[{id:'t',status:'completed',items:[{type:'agentMessage',text,phase:'final'},{type:'agentMessage',text:'x'.repeat(990)+' '+secrets[0],phase:'final'}]}]}));
  const r=await query('read',ID,{pipe,timeoutMs:1000});
  const output=r.turns[0].messages[0].text;
  for(const secret of secrets) assert.ok(!output.includes(secret),secret);
  assert.ok(!output.includes('fixture-secret'));
  assert.ok(!output.includes('fixture-password'));
  assert.ok(!output.includes('fixture-body'));
  assert.ok(output.includes('繁體中文 and English'));
  assert.ok(!r.turns[0].messages[1].text.includes('sk-'));
});

test('send checks exact live target then submits once without changing model settings',async t=>{
  const calls=[];
  const pipe=await fixture(t,req=>{
    calls.push(req);
    if(req.method==='tools/list')return catalog;
    if(req.params.tool==='read_thread')return response({thread:{id:ID,kind:'codex',title:'Synthetic target',status:{type:'active'}},turns:[]});
    assert.equal(req.params.tool,'send_message_to_thread');
    assert.deepEqual(req.params.arguments,{threadId:ID,hostId:'local',prompt:'  繼續原任務\nEnglish  '});
    return {success:true,contentItems:[{type:'inputText',text:'Message accepted'}]};
  });
  const r=await send(ID,'  繼續原任務\nEnglish  ',{pipe,timeoutMs:1000,expectedTitle:'Synthetic target'});
  assert.equal(r.deliveryState,'accepted');assert.equal(r.executionComplete,false);
  assert.equal(r.targetTitle,'Synthetic target');
  assert.equal(calls.filter(x=>x.params?.tool==='send_message_to_thread').length,1);
});
test('send rejects invalid input, wrong kind, and missing capabilities before mutation',async t=>{
  await assert.rejects(send('bad','hello'),/thread ID/);
  await assert.rejects(send(ID,'  '),/message/);
  await assert.rejects(send(ID,'a'.repeat(65537)),/message/);
  for(const kind of ['chatgpt','unknown']){
    const pipe=await fixture(t,req=>{
      if(req.method==='tools/list')return catalog;
      assert.equal(req.params.tool,'read_thread');
      return response({thread:{id:ID,kind,status:{type:'idle'}},turns:[]});
    });
    await assert.rejects(send(ID,'hello',{pipe,timeoutMs:500}),e=>e.deliveryState==='not-sent');
  }
  const pipe=await fixture(t,()=>({tools:[]}));
  await assert.rejects(send(ID,'hello',{pipe,timeoutMs:500}),e=>e.deliveryState==='not-sent');
});

test('mutation preflight rejects a wrong title or failed lookup before dispatch',async t=>{
  for(const operation of [send,stop])for(const mode of ['title','lookup','id','kind','state']){
    let submitted=0;
    const pipe=await fixture(t,req=>{
      if(req.method==='tools/list')return catalog;
      if(req.params.tool==='read_thread')return mode==='lookup'?{success:false,contentItems:[]}:
        response({thread:{id:mode==='id'?OWNER:ID,kind:mode==='kind'?'chatgpt':'codex',
          title:'Other conversation',status:mode==='state'?{}:{type:'idle'}},turns:[]});
      submitted++;return response({});
    });
    const options={pipe,timeoutMs:500,expectedTitle:'Synthetic target'};
    const invoke=()=>operation===send?send(ID,'test',options):stop(ID,options);
    await assert.rejects(invoke,e=>e.deliveryState==='not-sent'&&e.threadId===ID&&
      e.code===(mode==='title'?'TARGET_TITLE_MISMATCH':mode==='lookup'?'DESKTOP_REJECTED':'TARGET_LOOKUP_FAILED'));
    assert.equal(submitted,0);
  }
});
test('send timeout after dispatch is unknown and is never retried',async t=>{
  let submitted=0;
  const pipe=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    if(req.params.tool==='read_thread')return response({thread:{id:ID,kind:'codex',status:{type:'active',activeFlags:[]}},turns:[]});
    submitted++;return undefined;
  });
  await assert.rejects(send(ID,'hello',{pipe,timeoutMs:80}),e=>e.deliveryState==='unknown'&&e.automaticRetrySafe===false);
  assert.equal(submitted,1);
});
test('stop discovers actual owner and uses user-stop routing; no process killing',async t=>{
  let reads=0;const requests=[];
  const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:response({thread:{id:ID,kind:'codex',title:'stop target',status:{type:++reads===1?'active':'idle'}},turns:[{id:'old-turn',status:reads===1?'inProgress':'interrupted'}]}));
  const ipcPipe=await fixture(t,req=>{
    requests.push(req);
    if(req.method==='initialize')return {clientId:ID};
    if(req.method==='thread-owner-discovery')return {supportsUntrustedAppInput:true};
    assert.equal(req.method,'thread-follower-interrupt-turn');
    assert.equal(req.targetClientId,OWNER);assert.equal(req.version,3);
    assert.deepEqual(req.params,{conversationId:ID,mode:'user-stop'});
    return {ok:true,interruptedTurnId:'old-turn'};
  });
  const r=await stop(ID,{pipe,ipcPipe,timeoutMs:1000});
  assert.equal(r.stopConfirmed,true);assert.equal(r.deliveryState,'accepted');
  assert.equal(r.targetTitle,'stop target');
  assert.deepEqual(requests.map(x=>x.method),['initialize','thread-owner-discovery','thread-follower-interrupt-turn']);
});
test('stop detects partial goal-pause failure and uncertain delivery without retry',async t=>{
  for(const mode of ['partial','timeout']){
    let interrupts=0;
    const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:response({thread:{id:ID,kind:'codex',title:'stop target',status:{type:'active'}},turns:[]}));
    const ipcPipe=await fixture(t,req=>{
      if(req.method==='initialize')return {clientId:ID};
      if(req.method==='thread-owner-discovery')return {};
      interrupts++;
      return mode==='partial'?{ok:true,interruptedTurnId:'turn1',goalPauseError:'failed to pause'}:undefined;
    });
    if(mode==='partial'){
      const r=await stop(ID,{pipe,ipcPipe,timeoutMs:1000});
      assert.equal(r.deliveryState,'partial');assert.equal(r.stopConfirmed,false);
      assert.equal(r.targetTitle,'stop target');
    }else await assert.rejects(stop(ID,{pipe,ipcPipe,timeoutMs:80}),e=>e.deliveryState==='unknown');
    assert.equal(interrupts,1);
  }
});
test('existing read exposes related subagent IDs without tool prompts or new endpoints',async t=>{
  const child=randomUUID();
  const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:response({thread:{id:ID,status:{type:'idle'}},turns:[{id:'t',items:[
    {type:'collabAgentToolCall',receiverThreadIds:[child],prompt:'PRIVATE'},
    {type:'subAgentActivity',agentThreadId:child,kind:'started'}]}]}));
  const r=await query('read',ID,{pipe,timeoutMs:1000});
  assert.deepEqual(r.turns[0].subagents,[{threadId:child}]);
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
});
test('stop notLoaded target is a no-op and never opens the control pipe',async t=>{
  const pipe=await fixture(t,req=>req.method==='tools/list'?catalog:response({thread:{id:ID,kind:'codex',title:'stop target',status:{type:'notLoaded'}},turns:[]}));
  const r=await stop(ID,{pipe,ipcPipe:'invalid-unused-pipe',timeoutMs:500});
  assert.equal(r.deliveryState,'not-active');
  assert.equal(r.targetTitle,'stop target');
});
test('CLI preserves UTF-8 file text and resume uses an explicit continuation prompt',async t=>{
  const fs=require('node:fs'),path=require('node:path');
  fs.mkdirSync(path.join(__dirname,'tmp'),{recursive:true});
  const file=path.join(__dirname,'tmp',`message-${randomUUID()}.txt`);
  fs.writeFileSync(file,'繁體中文\nKeep $() literal');t.after(()=>fs.unlinkSync(file));
  const {main}=require('./cli.cjs');const calls=[];
  const api={stop:async id=>({id}),query:async()=>({})};
  const deliver=async(...args)=>{calls.push(args);return {deliveryState:'accepted'};};
  await main(['send',ID,file,'cli-test-1','exact title'],api,deliver);
  assert.equal(calls[0][1].message,'繁體中文\nKeep $() literal');
  await main(['resume',ID,'cli-test-2','exact title'],api,deliver);assert.equal(calls[1][0],'resume');
  assert.deepEqual(await main(['stop',ID],api),{id:ID});
  await assert.rejects(main(['send',ID],api),/Usage/);
  await assert.rejects(main(['status',ID,'extra'],api),/Usage/);
  fs.writeFileSync(file,Buffer.alloc(65537));
  await assert.rejects(main(['send',ID,file,'cli-test-3','exact title'],api,deliver),/64 KiB/);
  fs.writeFileSync(file,Buffer.from([255]));
  await assert.rejects(main(['send',ID,file,'cli-test-3','exact title'],api,deliver),/encoded data|encoding/i);
  assert.equal(calls.length,2);
});

test('compact list pages a pinned-first snapshot without inventing native cursors',async t=>{
  const title='Long title '.repeat(200);let args;
  const pipe=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    args=req.params.arguments;
    return response({pinnedThreads:[{id:OWNER,title:'pinned'}],threads:[
      {id:ID,title,status:{type:'idle'},summary:'PRIVATE'}, {id:randomUUID(),title:'third'}]});
  });
  const r=await query('list','',{pipe,limit:1,offset:1});
  assert.deepEqual(args,{limit:2});
  assert.equal(r.threads.length,1);
  assert.equal(r.threads[0].id,ID);
  assert.equal(r.threads[0].title,undefined);
  assert.equal(r.threads[0].title_preview,title.slice(0,120));
  assert.equal(r.threads[0].full_title_required,'codex_status/codex_read');
  assert.equal(r.pagination.nextOffset,2);
  assert.equal(r.pagination.nativeCursorSupported,false);
  assert.equal(r.pagination.cursor,undefined);
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
  const compact=await query('list','',{pipe});
  assert.equal(compact.threads[1].title_preview,title.slice(0,120));
  assert.equal(compact.pagination.limit,10);
  assert.equal(compact.pagination.hasMore,false);
  assert.equal(compact.pagination.nextOffset,null);
  const full=await query('list','',{pipe,compact:false,limit:1,offset:1});
  assert.equal(full.threads[0].title,title);
  const status=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    assert.equal(req.params.tool,'read_thread');
    return response({thread:{id:ID,kind:'codex',title,status:{type:'idle'}},turns:[]});
  });
  assert.equal((await query('status',ID,{pipe:status})).thread.title,title);
  await assert.rejects(send(ID,'synthetic',{pipe:status,expectedTitle:r.threads[0].title_preview}),
    e=>e.code==='TARGET_TITLE_MISMATCH'&&e.deliveryState==='not-sent');
  await assert.rejects(stop(ID,{pipe:status,expectedTitle:r.threads[0].title_preview}),
    e=>e.code==='TARGET_TITLE_MISMATCH'&&e.deliveryState==='not-sent');
});

test('read forwards native paging, keeps recent messages and final_answer without duplicating text',async t=>{
  let args;const children=Array.from({length:4},()=>randomUUID());
  const page={nextCursor:'opaque-native',hasMore:true};
  const pipe=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    args=req.params.arguments;
    return response({thread:{id:ID,status:{type:'idle'}},page,turns:[{id:'t',status:'completed',items:[
      {type:'userMessage',text:'older'}, {type:'agentMessage',phase:'commentary',text:'recent'},
      {type:'agentMessage',phase:'final_answer',text:'x'.repeat(100)},
      {type:'agentMessage',phase:'analysis',text:'PRIVATE'},
      {type:'collabAgentSpawnToolCall',newThreadId:children[0],prompt:'PRIVATE'},
      {type:'collabAgentInteractionToolCall',receiverThreadId:children[1]},
      {type:'collabAgentWaitToolCall',agentsStates:{[children[2]]:{status:'completed'}}},
      {type:'collabAgentToolCall',receiverThreadIds:[children[3],children[0],'invalid']}
    ]}]});
  });
  const r=await query('read',ID,{pipe,cursor:'previous-native',turn_limit:1,message_limit:2,max_chars:20});
  assert.deepEqual(args,{threadId:ID,hostId:'local',turnLimit:1,cursor:'previous-native',includeOutputs:false,maxOutputCharsPerItem:20});
  assert.deepEqual(r.page,page);
  assert.deepEqual(r.turns[0].messages.map(m=>m.text),['recent','x'.repeat(20)]);
  assert.equal(r.turns[0].messages[1].phase,'final_answer');
  assert.deepEqual(r.turns[0].subagents,children.map(threadId=>({threadId})));
  assert.equal(r.latestTurn.messages,undefined);
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
});

test('status requests no message text and does not equate idle with task completion',async t=>{
  const pipe=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    assert.deepEqual(req.params.arguments,{threadId:ID,hostId:'local',turnLimit:2,includeOutputs:false,maxOutputCharsPerItem:0});
    return response({thread:{id:ID,status:{type:'idle'}},turns:[{id:'old',status:'completed',items:[{type:'agentMessage',text:'PRIVATE'}]}]});
  });
  const r=await query('status',ID,{pipe});
  assert.equal(r.thread.status.type,'idle');
  assert.equal(r.executionComplete,undefined);
  assert.equal(r.latestTurn.id,'old');
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
});

test('query options reject invalid bounds before any connection',async()=>{
  for(const [command,options] of [
    ['list',{limit:0}],['list',{limit:101}],['list',{offset:-1}],['list',{offset:1001}],
    ['list',{compact:'true'}],['list',{cursor:'fake'}],['read',{turn_limit:0}],
    ['read',{turn_limit:11}],['read',{message_limit:0}],['read',{message_limit:21}],
    ['read',{max_chars:0}],['read',{max_chars:2001}],['read',{max_chars:1.5}],['read',{cursor:42}]
  ]) await assert.rejects(query(command,command==='list'?'':ID,{pipe:'unused',...options}),/Invalid query option/);
});

test('send receipt carries the observed baseline without claiming a newly completed turn',async t=>{
  const pipe=await fixture(t,req=>{
    if(req.method==='tools/list')return catalog;
    if(req.params.tool==='read_thread')return response({thread:{id:ID,kind:'codex',title:'target',status:{type:'idle'}},
      turns:[{id:'prior',status:'completed',completedAt:Math.floor(Date.now()/1000),items:[{type:'agentMessage',text:'PRIVATE'}]}]});
    return response({accepted:true});
  });
  const r=await send(ID,'synthetic',{pipe,expectedTitle:'target'});
  assert.equal(r.turnBaseline.id,'prior');assert.equal(r.turnBaseline.status,'completed');assert.ok(Number.isFinite(r.turnBaseline.completedAt));
  assert.equal(r.executionComplete,false);
  assert.deepEqual(r.followUp,{tool:'codex_status',thread_id:ID,suggestedIntervalSeconds:30});
  assert.ok(!JSON.stringify(r).includes('PRIVATE'));
});

test('MCP adapter forwards query options while preserving exact mutation title',async()=>{
  const fs=require('node:fs'),vm=require('node:vm'),{EventEmitter}=require('node:events');
  for(const action of ['list','read','status','send','resume','stop']){
    const stdin=new EventEmitter();stdin.setEncoding=()=>{};let call;
    const api={query:async(...args)=>{call=args;return {};},send:async(...args)=>{call=args;return {};},stop:async(...args)=>{call=args;return {};}};
    vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../codex.cjs'),'utf8'),{
      require:name=>name==='node:crypto'?require('node:crypto'):name==='./codex-status/diagnostics.cjs'?require('./diagnostics.cjs'):name==='node:fs'?{readFileSync:()=>JSON.stringify({callerThreadId:ID})}:name==='node:path'?require('node:path'):api,__dirname:require('node:path').join(__dirname,'..'),Buffer,process:{stdin,stdout:{write(){}},exit(){throw Error('unexpected exit');}}
    });
    stdin.emit('data',JSON.stringify({action,thread_id:ID,expected_title:'exact title',message:'synthetic',
      compact:false,limit:3,offset:2,cursor:'native',turn_limit:1,message_limit:2,max_chars:30}));
    await stdin.listeners('end')[0]();
    const options=call.at(-1);
    assert.ok(UUIDForTest(options.callerThreadId));
    if(['list','read','status'].includes(action)){
      assert.equal(options.max_chars,30);assert.equal(options.turn_limit,1);assert.equal(options.message_limit,2);
      assert.equal(options.compact,false);assert.equal(options.limit,3);assert.equal(options.offset,2);assert.equal(options.cursor,'native');
    }else assert.equal(options.expectedTitle,'exact title');
  }
});
function UUIDForTest(value){return /^[0-9a-f-]{36}$/.test(value);}

test('status supplies native predecessor identity to detect intervening turns',async t=>{
  const pipe=await fixture(t,r=>{
    if(r.method==='tools/list')return catalog;
    assert.equal(r.params.arguments.turnLimit,2);
    return response({thread:{id:ID,kind:'codex',status:{type:'idle'}},
      turns:[{id:'compact-new',status:'completed',completedAt:1700000000,items:[{type:'contextCompaction'}]},
        {id:'previous-turn',status:'completed',completedAt:1699999999,items:[]}]});
  });
  const result=await query('status',ID,{pipe});
  assert.deepEqual(result.recentTurnIds,['compact-new','previous-turn']);
  assert.equal(result.turns,undefined);
});

test('status exposes verified native compaction metadata without message text',async t=>{
  const pipe=await fixture(t,r=>r.method==='tools/list'?catalog:response({thread:{id:ID,kind:'codex',status:{type:'idle'}},
    turns:[{id:'compact-turn',status:'completed',completedAt:1700000000,items:[{id:'c1',type:'contextCompaction'}]}]}));
  const r=await query('status',ID,{pipe});
  assert.equal(r.compactState.lastTurnIsCompaction,true);
  assert.equal(r.compactState.completedAt,1700000000);
  assert.equal(r.compactState.turnId,'compact-turn');
});

test('manual native compact checks idle baseline twice and only acknowledges receipt',async t=>{
  const {compactStart}=require('./bridge.cjs');let reads=0,submitted=0;
  const pipe=await fixture(t,r=>{
    if(r.method==='tools/list')return catalog;
    reads++;return response({thread:{id:ID,kind:'codex',title:'target',status:{type:'idle'}},
      turns:[{id:'prior',status:'completed',completedAt:1700000000,items:[]}]});
  });
  const ipc=await fixture(t,r=>{
    if(r.method==='initialize')return {clientId:ID};
    if(r.method==='thread-owner-discovery')return {};
    assert.equal(r.method,'thread-follower-compact-thread');assert.equal(r.version,1);
    assert.deepEqual(r.params,{conversationId:ID});submitted++;return {ok:true};
  });
  const r=await compactStart(ID,{pipe,ipcPipe:ipc,expectedTitle:'target',expectedBaselineTurnId:'prior'});
  assert.equal(reads,2);assert.equal(submitted,1);assert.equal(r.deliveryState,'accepted');
  assert.equal(r.compactComplete,false);
  await assert.rejects(compactStart(ID,{pipe,ipcPipe:ipc,expectedTitle:'target',expectedBaselineTurnId:'wrong'}),e=>e.deliveryState==='not-sent');
  assert.equal(submitted,1);
});

test('compact rejects another turn or active state appearing during owner discovery',async t=>{
  const {compactStart}=require('./bridge.cjs');let reads=0,submitted=0,mode='changed';
  const pipe=await fixture(t,r=>{
    if(r.method==='tools/list')return catalog;
    reads++;
    return response({thread:{id:ID,kind:'codex',title:'target',status:{type:reads%2===0&&mode==='active'?'active':'idle'}},
      turns:[{id:reads%2===0&&mode==='changed'?'other':'prior',status:'completed',completedAt:1700000000,items:[]}]});
  });
  const ipc=await fixture(t,r=>{
    if(r.method==='initialize')return {clientId:ID};
    if(r.method==='thread-owner-discovery')return {};
    submitted++;return {ok:true};
  });
  for(mode of ['changed','active'])await assert.rejects(
    compactStart(ID,{pipe,ipcPipe:ipc,expectedTitle:'target',expectedBaselineTurnId:'prior'}),
    e=>e.code==='TARGET_CHANGED'&&e.deliveryState==='not-sent');
  assert.equal(reads,4);assert.equal(submitted,0);
});
