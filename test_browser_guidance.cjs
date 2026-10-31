const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const guide = fs.readFileSync(path.join(__dirname,
  'skills/software-development/hermes-web-mcp-workflows/references/browser.md'), 'utf8');
const fence = String.fromCharCode(96).repeat(3);
const code = guide.split(fence+'js\n')[1].split('\n'+fence)[0];

function record(id, author) {
  const node = {dataset:{recordId:id,ready:'true'}};
  const fields = ['author','body','time'].map(name => ({
    dataset:{field:name},textContent:name==='author'?author:name,
    closest:()=>node,
  }));
  node.querySelectorAll=()=>fields;
  return node;
}

async function observe(roots, expectReady) {
  let result;
  const scope={querySelectorAll:()=>roots};
  const context={recordId:'wanted',mainSelector:'main',
    document:{querySelectorAll:selector=>selector==='main'?[scope]:roots},
    page:{
      waitForFunction:async(fn,args)=>{
        if(!fn(args)) throw new Error('not ready');
      },
      evaluate:async(fn,args)=>fn(args),
    },
    console:{log:value=>{result=value;}},
  };
  const execution = vm.runInNewContext('(async()=>{'+code+'})()', context);
  if(expectReady) {
    await execution;
    assert.equal(result.fields.author.text,'correct');
    assert.equal(result.ready,true);
  } else {
    await assert.rejects(execution,/not ready/);
  }
}

test('single target is ready, missing or duplicate stable identities fail closed', async()=>{
  await observe([record('wanted','correct')], true);
  await observe([record('other','wrong')], false);
  await observe([record('wanted','wrong'),record('wanted','correct')], false);
});
