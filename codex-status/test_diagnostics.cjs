process.env.HERMES_CODEX_CALLER_THREAD_ID='11111111-1111-4111-8111-111111111111';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const net=require('node:net');
const {randomUUID}=require('node:crypto');
const {query,selectPipe}=require('./bridge.cjs');
const {send}=require('./bridge.cjs');
const ID=process.env.HERMES_CODEX_CALLER_THREAD_ID;
async function fixture(t,handler){
  const pipe=process.platform==='win32'?`\\\\.\\pipe\\diagnostic-${randomUUID()}`:`/tmp/diag-${randomUUID()}.sock`;
  const sockets=new Set();const server=net.createServer(s=>{
    sockets.add(s);s.on('close',()=>sockets.delete(s));let buf=Buffer.alloc(0);
    s.on('data',chunk=>{buf=Buffer.concat([buf,chunk]);while(buf.length>=4){
      const n=buf.readUInt32LE();if(buf.length<n+4)return;
      const req=JSON.parse(buf.subarray(4,n+4));buf=buf.subarray(n+4);
      const result=handler(req,s);if(result===undefined)continue;
      const b=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:req.id,result}));const h=Buffer.alloc(4);h.writeUInt32LE(b.length);s.write(Buffer.concat([h,b]));
    }});
  });await new Promise(r=>server.listen(pipe,r));
  t.after(async()=>{for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));});return pipe;
}
const catalog={tools:[{name:'read_thread',namespace:'codex_app'},{name:'list_threads',namespace:'codex_app'}]};
test('native auth, writer and history errors remain distinguishable without private text',()=>{
  const {remoteFault,envelope}=require('./diagnostics.cjs');
  for(const [message,code] of [
    ['Your access token could not be refreshed because you have since logged out or signed in to another account.','AUTHENTICATION_REQUIRED'],
    ['This is open in another app. Close it there to continue here.','BACKEND_WRITER_CONFLICT'],
    ['history_fetch_failed','THREAD_HISTORY_UNAVAILABLE']]){
    const result=envelope(remoteFault({error:{message:message+' PRIVATE-SECRET'}},'request',{requestId:'existing-request'}));
    assert.equal(result.errorCode,code);
    assert.equal(result.requestId,'existing-request');
    assert.ok(!JSON.stringify(result).includes('PRIVATE-SECRET'));
  }
});
test('discovery distinguishes missing and multiple instances',()=>{
  assert.throws(()=>selectPipe([]),e=>e.code==='DESKTOP_PIPE_MISSING'&&e.stage==='discover');
  assert.throws(()=>selectPipe([`codex-app-tools-${ID}`,`codex-app-tools-${randomUUID()}`]),e=>e.code==='DESKTOP_PIPE_AMBIGUOUS');
});
test('read failures retain safe classified context and never become idle',async t=>{
  for(const [native,expected] of [['thread notLoaded','THREAD_NOT_LOADED'],['thread not found','THREAD_NOT_FOUND'],
    ['No Codex thread found for threadId: fixture. Hosts without a readable match: PRIVATE','THREAD_NOT_FOUND'],['bridge_busy','BRIDGE_BUSY'],['invalid limit argument','NATIVE_ARGUMENTS_INVALID']]){
    const pipe=await fixture(t,r=>r.method==='tools/list'?catalog:{success:false,error:{code:native,message:'C:\\private\\token secret-value'}});
    await assert.rejects(query('status',ID,{pipe}),e=>{
      assert.equal(e.code,expected);assert.equal(e.stage,'request');assert.equal(e.tool,'read_thread');
      assert.equal(e.requestState,'rejected');assert.ok(e.requestId);assert.ok(e.observedAt);
      assert.equal(e.retryable,expected==='BRIDGE_BUSY');
      assert.ok(!JSON.stringify(e).includes('secret-value'));return true;
    });
  }
});
test('handshake timeout and malformed payload identify separate stages',async t=>{
  const silent=await fixture(t,()=>undefined);
  await assert.rejects(query('list','',{pipe:silent,timeoutMs:40}),e=>e.code==='DESKTOP_TIMEOUT'&&e.stage==='handshake'&&e.timedOut&&e.automaticRetrySafe);
  const malformed=await fixture(t,r=>r.method==='tools/list'?catalog:{success:true,contentItems:[{type:'inputText',text:'invalid'}]});
  await assert.rejects(query('read',ID,{pipe:malformed}),e=>e.code==='PROTOCOL_INVALID'&&e.stage==='parse'&&e.tool==='read_thread');
});
module.exports={fixture,catalog,ID};

test('active send keeps native queue and rejects a changed active baseline',async t=>{
  let sent=0;
  const pipe=await fixture(t,r=>{
    if(r.method==='tools/list')return {tools:[...catalog.tools,{name:'send_message_to_thread',namespace:'codex_app'}]};
    if(r.params.tool==='read_thread')return {success:true,contentItems:[{type:'inputText',text:JSON.stringify({
      thread:{id:ID,kind:'codex',title:'target',status:{type:'active'}},turns:[{id:'live',status:'inProgress'}]})}]};
    sent++;return {success:true};
  });
  assert.equal((await send(ID,'test',{pipe,expectedTitle:'target',expectedBaselineTurnId:'live',activeBaseline:true})).deliveryState,'accepted');
  await assert.rejects(send(ID,'test',{pipe,expectedTitle:'target',expectedBaselineTurnId:'old',activeBaseline:true}),e=>e.code==='TARGET_CHANGED');
  assert.equal(sent,1);
});

test('compact policy uses real completion time and cannot mistake stale notLoaded for active',()=>{
  const {preflight}=require('./compact.cjs');const now=Date.now();
  const state=(type,turn)=>({thread:{status:{type}},turns:[turn]});
  assert.equal(preflight(state('idle',{id:'t',status:'completed',completedAt:(now-1800001)/1000}),now).required,false);
  assert.equal(preflight(state('idle',{id:'t',status:'completed',completedAt:new Date(now-1800000).toISOString()}),now).required,false);
  assert.equal(preflight(state('active',{id:'t',status:'inProgress',completedAt:null}),now).required,false);
  assert.throws(()=>preflight(state('notLoaded',{id:'t',status:'inProgress'})),e=>e.code==='THREAD_STATE_UNCONFIRMED');
  assert.throws(()=>preflight(state('idle',{id:'t',status:'completed',completedAt:null})),e=>e.code==='COMPLETION_TIME_UNCONFIRMED');
  assert.equal(preflight(state('idle',{id:'compact',status:'completed',completedAt:(now-3600000)/1000,
    items:[{type:'contextCompaction'}]}),now).required,false);
});

test('send fails closed for unavailable or missing actual completion time',async t=>{
  for(const state of ['notLoaded','idle']){
    let sent=0;
    const pipe=await fixture(t,r=>{
      if(r.method==='tools/list')return {tools:[...catalog.tools,{name:'send_message_to_thread',namespace:'codex_app'}]};
      if(r.params.tool==='read_thread')return {success:true,contentItems:[{type:'inputText',text:JSON.stringify({thread:{id:ID,kind:'codex',status:{type:state}},turns:[{id:'t',status:'completed'}]})}]};
      sent++;return {success:true};
    });
    await assert.rejects(send(ID,'test',{pipe}),e=>e.deliveryState==='not-sent'&&['THREAD_STATE_UNCONFIRMED','COMPLETION_TIME_UNCONFIRMED'].includes(e.code));
    assert.equal(sent,0);
  }
});
