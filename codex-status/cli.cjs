const bridge = require('./bridge.cjs');
const fs=require('node:fs');
const {envelope}=require('./diagnostics.cjs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
function persistentCall(action,args){
  const configured=process.env.HERMES_MCP_PYTHON;
  if(configured!==undefined&&(!path.isAbsolute(configured)||!fs.existsSync(configured)||
    !fs.statSync(configured).isFile()||(process.platform==='win32'&&!/\.(exe|com)$/i.test(configured)))){
    return {ok:false,errorCode:'PYTHON_NOT_FOUND',deliveryState:'not-sent',automaticRetrySafe:false,
      error:'HERMES_MCP_PYTHON 必須是現有 Python 執行檔的絕對路徑；未派送。'};
  }
  const child=spawnSync(configured??'python',
    [path.join(__dirname,'../codex_adapter.py')],{input:JSON.stringify({action,...args}),encoding:'utf8',
      windowsHide:true,timeout:650000,maxBuffer:200000});
  try{return JSON.parse(child.stdout);}
  catch{return {ok:false,errorCode:child.error?.code==='ENOENT'?'PYTHON_NOT_FOUND':'CLI_OUTCOME_UNCONFIRMED',
    deliveryState:child.error?.code==='ENOENT'?'not-sent':'unknown',automaticRetrySafe:false,
    operation_id:args.operation_id,error:'CLI 未取得有效回執；以相同 operation_id 查詢紀錄，勿重新派送。'};}
}
function messageFile(path){
  const fd=fs.openSync(path,'r'),buffer=Buffer.alloc(65537);let size=0;
  try{
    if(!fs.fstatSync(fd).isFile())throw Error('Message file must be a regular UTF-8 file');
    while(size<buffer.length){const n=fs.readSync(fd,buffer,size,buffer.length-size,null);if(!n)break;size+=n;}
    if(size>65536)throw Error('Message file exceeds 64 KiB');
    return new TextDecoder('utf-8',{fatal:true}).decode(buffer.subarray(0,size));
  }finally{fs.closeSync(fd);}
}
async function main(args,api=bridge,deliver=persistentCall){
  const [command,id,file]=args;
  if(command==='list'&&args.length===1)return api.query(command,id);
  if(['status','read'].includes(command)&&args.length===2)return deliver(command,{thread_id:id});
  if(command==='stop'&&args.length===2){
    const before=await deliver('status',{thread_id:id});
    if(before.errorCode||before.thread?.id!==id||!before.thread.title)return before;
    return deliver('stop',{thread_id:id,expected_title:before.thread.title});
  }
  if(['operation-status','delivery-status'].includes(command)&&args.length===2)
    return deliver(command.replace('-','_'),{operation_id:id});
  if(command==='send'&&args.length===5)return deliver('send',{
    thread_id:id,message:messageFile(file),operation_id:args[3],expected_title:args[4]});
  if(command==='resume'&&(args.length===4||args.length===5))return deliver('resume',{
    thread_id:id,operation_id:args[2],expected_title:args[3],...(args[4]?{message:messageFile(args[4])}:{})});
  throw Error('Usage: node cli.cjs list | status/read/stop THREAD_ID | operation-status/delivery-status OPERATION_ID | send THREAD_ID MESSAGE_FILE OPERATION_ID EXPECTED_TITLE | resume THREAD_ID OPERATION_ID EXPECTED_TITLE [MESSAGE_FILE]');
}
if(require.main===module){
  main(process.argv.slice(2)).then(result => {
    console.log(JSON.stringify(result,null,2));
    if(result.ok===false||result.error)process.exitCode=1;
    if(result.deliveryState==='partial'||result.stopConfirmed===false)process.exitCode=2;
  })
    .catch(error => {
      console.error(JSON.stringify(envelope(error,{liveStatusConfirmed:false})));
      process.exitCode = 1;
    });
}
module.exports={main};
