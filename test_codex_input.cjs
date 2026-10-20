const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
function run(value) {
  const result = spawnSync(process.execPath,[path.join(__dirname,'codex.cjs')],
    {input:JSON.stringify(value),encoding:'utf8',timeout:10000,maxBuffer:200000});
  return JSON.parse(result.stdout);
}
test('valid UTF-8-sized title reaches dispatch validation without touching desktop',()=>{
  const result=run({action:'unsupported',expected_title:'漢'.repeat(40000)});
  assert.equal(result.error,'unsupported action');
  assert.equal(result.deliveryState,'not-sent');
});
test('oversize input is rejected with structured not-sent outcome',()=>{
  const result=run({action:'unsupported',message:'x'.repeat(1100000)});
  assert.equal(result.errorCode,'INPUT_TOO_LARGE');
  assert.equal(result.deliveryState,'not-sent');
});

test('clean checkout reports missing local routing configuration without an external sibling',t=>{
  fs.mkdirSync(path.join(__dirname,'tmp'),{recursive:true});
  const root=fs.mkdtempSync(path.join(__dirname,'tmp','checkout-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  fs.copyFileSync(path.join(__dirname,'codex.cjs'),path.join(root,'codex.cjs'));
  if(fs.existsSync(path.join(__dirname,'codex-status')))
    fs.cpSync(path.join(__dirname,'codex-status'),path.join(root,'codex-status'),{recursive:true,filter:p=>path.basename(p)!=='tmp'});
  const r=spawnSync(process.execPath,[path.join(root,'codex.cjs')],
    {input:JSON.stringify({action:'list'}),encoding:'utf8',timeout:10000});
  assert.ok(r.stdout.startsWith('{'),'clean clone must return a structured configuration error');
  const result=JSON.parse(r.stdout);
  assert.equal(result.errorCode,'CODEX_CONTEXT_REQUIRED');
  assert.equal(result.deliveryState,'not-sent');
});
