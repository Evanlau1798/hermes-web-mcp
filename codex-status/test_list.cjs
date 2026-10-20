const {test}=require('node:test');
const assert=require('node:assert/strict');
const net=require('node:net');
const {randomUUID}=require('node:crypto');
const {query}=require('./bridge.cjs');
const CALLER='11111111-1111-4111-8111-111111111111';
async function fixture(t,maximum=50,count=50,options={}){
  const pipe=process.platform==='win32'?`\\\\.\\pipe\\hermes-list-${randomUUID()}`:`/tmp/hermes-${randomUUID()}.sock`;
  const requests=[],sockets=new Set();
  const server=net.createServer(s=>{
    sockets.add(s);s.on('close',()=>sockets.delete(s));let buffer=Buffer.alloc(0);
    s.on('data',chunk=>{buffer=Buffer.concat([buffer,chunk]);while(buffer.length>=4){
      const n=buffer.readUInt32LE();if(buffer.length<n+4)break;
      const req=JSON.parse(buffer.subarray(4,n+4));buffer=buffer.subarray(n+4);let result;
      if(req.method==='tools/list')result=options.nullCatalog?null:{tools:[{name:'list_threads',namespace:'codex_app',inputSchema:{properties:{limit:options.missingMaximum?{}:{maximum}}}}]};
      else{const limit=req.params.arguments.limit;requests.push(limit);
        result=limit>maximum?{success:false,error:{message:'Invalid limit argument'}}:{success:true,contentItems:[{type:'inputText',text:JSON.stringify({pinnedThreads:[],threads:Array.from({length:Math.min(limit,count)},(_,i)=>({id:`fixture-${i}`,title:`Title ${i}`,kind:'codex'}))})}]};}
      const b=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:req.id,result})),h=Buffer.alloc(4);h.writeUInt32LE(b.length);s.write(Buffer.concat([h,b]));
    }});
  });
  await new Promise(r=>server.listen(pipe,r));
  t.after(async()=>{for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));});
  return {pipe,requests,callerThreadId:CALLER,timeoutMs:1000};
}
test('large public limit uses native maximum with explicit bounded pagination',async t=>{
  const f=await fixture(t);const r=await query('list','',{...f,limit:100,offset:10});
  assert.deepEqual(f.requests,[50]);assert.equal(r.threads.length,40);
  assert.equal(r.pagination.nativeLimit,50);assert.equal(r.pagination.mode,'bounded-snapshot');
  assert.equal(r.pagination.hasMore,null);assert.equal(r.pagination.nextOffset,null);
  assert.equal(r.pagination.snapshotTruncated,true);
});
test('advertised smaller limit is respected and deep offset is not false exhaustion',async t=>{
  const f=await fixture(t,20,50);
  await assert.rejects(query('list','',{...f,limit:20,offset:20}),e=>e.code==='NATIVE_PAGINATION_LIMIT'&&e.retryable===false&&e.automaticRetrySafe===true);
  assert.deepEqual(f.requests,[20]);
});
test('truly exhausted native snapshot permits empty later pages',async t=>{
  const f=await fixture(t,50,10);const r=await query('list','',{...f,limit:100,offset:100});
  assert.deepEqual(f.requests,[50]);assert.deepEqual(r.threads,[]);
  assert.equal(r.pagination.hasMore,false);assert.equal(r.pagination.snapshotTruncated,false);
});
test('missing maximum falls back to 50 and null catalog has a classified error',async t=>{
  const f=await fixture(t,50,50,{missingMaximum:true});
  const r=await query('list','',{...f,limit:100});
  assert.deepEqual(f.requests,[50]);assert.equal(r.pagination.nativeLimit,50);
  const bad=await fixture(t,50,50,{nullCatalog:true});
  await assert.rejects(query('list','',bad),e=>e.code==='CAPABILITY_UNAVAILABLE'&&e.retryable===false);
});
