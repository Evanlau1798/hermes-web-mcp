// JSON travels over stdin, never through a shell or command arguments.
const bridge = require('./codex-status/bridge.cjs');
const fs = require('node:fs');
const path = require('node:path');
const {envelope} = require('./codex-status/diagnostics.cjs');
const {randomUUID} = require('node:crypto');
let input='',inputBytes=0,oversized=false;
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{
  inputBytes+=Buffer.byteLength(chunk);
  if(inputBytes>1048576){oversized=true;input='';}
  else if(!oversized)input+=chunk;
});
process.stdin.on('end',async()=>{
  try{
    if(oversized)throw Object.assign(Error('Codex input exceeds 1 MiB; operation not dispatched'),
                                    {code:'INPUT_TOO_LARGE',deliveryState:'not-sent'});
    const {action,thread_id,message,expected_title,expected_baseline_turn_id,skip_compaction,compact,limit,offset,cursor,turn_limit,message_limit,max_chars}=JSON.parse(input);
    if(!['list','status','read','stop','send','resume','compact_start'].includes(action))throw Error('unsupported action');
    let options;
    try{
      options=JSON.parse(fs.readFileSync(path.join(__dirname,'runtime/codex-context.json'),'utf8'));
      if(!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(options.callerThreadId))throw Error('Invalid routing context');
      options={callerThreadId:options.callerThreadId};
    }catch{throw Object.assign(Error('Configure runtime/codex-context.json with an existing local Desktop routing context; operation not dispatched'),{code:'CODEX_CONTEXT_REQUIRED'});}
    const targetOptions={...options,expectedTitle:expected_title,expectedBaselineTurnId:expected_baseline_turn_id,skipCompaction:skip_compaction===true};
    let result;
    if(['list','status','read'].includes(action))result=await bridge.query(action,thread_id,
      {...options,compact,limit,offset,cursor,turn_limit,message_limit,max_chars});
    else if(action==='stop')result=await bridge.stop(thread_id,targetOptions);
    else if(action==='compact_start')result=await bridge.compactStart(thread_id,targetOptions);
    else if(['send','resume'].includes(action))result=await bridge.send(thread_id,message??'使用者要求續跑：請依照這段對話的既有目標與最新指示，從目前進度繼續。',targetOptions);
    else throw Error('unsupported action');
    process.stdout.write(JSON.stringify(result));
  }catch(e){process.stdout.write(JSON.stringify(envelope(e,{error:e instanceof SyntaxError?'輸入不是有效 JSON；未派送。':e.message,stage:'validate',source:'hermes-codex-adapter',
    requestId:'hermes-'+randomUUID(),observedAt:new Date().toISOString(),requestState:'not-sent',timedOut:false,disconnected:false,
    suggestedAction:'依錯誤訊息修正輸入或本機路由設定，先查證目標再決定後續操作。',
    deliveryState:'not-sent',automaticRetrySafe:false})));process.exitCode=1;}
});
