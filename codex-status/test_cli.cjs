const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {randomUUID}=require('node:crypto');
const {main}=require('./cli.cjs');
const {spawnSync}=require('node:child_process');
test('CLI uses configured Python outside a fixed Hermes installation without Desktop access',t=>{
  const root=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'hermes cli '));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const cliDir=path.join(root,'codex-status');fs.mkdirSync(cliDir);
  fs.copyFileSync(path.join(__dirname,'cli.cjs'),path.join(cliDir,'cli.cjs'));
  fs.writeFileSync(path.join(cliDir,'bridge.cjs'),'module.exports={}');
  fs.writeFileSync(path.join(cliDir,'diagnostics.cjs'),'exports.envelope=e=>({ok:false,error:e.message})');
  // Node stands in for Python; this adapter only echoes synthetic stdin.
  fs.writeFileSync(path.join(root,'codex_adapter.py'),
    'let s="";process.stdin.on("data",c=>s+=c);process.stdin.on("end",()=>console.log(JSON.stringify({ok:true,fixture:JSON.parse(s)})));');
  const result=spawnSync(process.execPath,[path.join(cliDir,'cli.cjs'),'resume','fixture-thread','fixture-operation','fixture title'],
    {env:{...process.env,HERMES_MCP_PYTHON:process.execPath},encoding:'utf8',timeout:10000});
  assert.equal(result.status,0,result.stderr||result.stdout);
  assert.equal(JSON.parse(result.stdout).fixture.expected_title,'fixture title');
  for(const value of ['', 'python.exe',root,path.join(root,'missing.exe')]){
    const invalid=spawnSync(process.execPath,[path.join(cliDir,'cli.cjs'),'resume','fixture-thread','fixture-operation','fixture title'],
      {env:{...process.env,HERMES_MCP_PYTHON:value},encoding:'utf8',timeout:10000});
    assert.equal(invalid.status,1);
    assert.equal(JSON.parse(invalid.stdout).deliveryState,'not-sent');
  }
});
test('CLI send/resume require operation IDs and route through persistent adapter',async t=>{
  fs.mkdirSync(path.join(__dirname,'tmp'),{recursive:true});
  const file=path.join(__dirname,'tmp',`cli-${randomUUID()}.txt`);
  fs.writeFileSync(file,'繁體中文 $() literal');t.after(()=>fs.unlinkSync(file));
  const calls=[];const api={send:()=>assert.fail('raw bridge.send must never be used')};
  const deliver=async(...args)=>{calls.push(args);return {deliveryState:'accepted'};};
  await main(['send','target',file,'operation-123','exact title'],api,deliver);
  assert.deepEqual(calls[0],['send',{thread_id:'target',message:'繁體中文 $() literal',operation_id:'operation-123',expected_title:'exact title'}]);
  await main(['resume','target','operation-456','exact title'],api,deliver);
  assert.equal(calls[1][0],'resume');assert.equal(calls[1][1].operation_id,'operation-456');
  await assert.rejects(main(['send','target',file],api,deliver),/Usage/);
  await assert.rejects(main(['resume','target'],api,deliver),/Usage/);
  assert.equal(calls.length,2);
});
