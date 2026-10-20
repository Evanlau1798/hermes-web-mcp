const net=require('node:net');
const {randomUUID}=require('node:crypto');
const {fault,remoteFault,classify}=require('./diagnostics.cjs');
const MAX_FRAME=8*1024*1024;
function connect(pipe,timeoutMs,ipc=false){
  const socket=net.createConnection(pipe);
  let buf=Buffer.alloc(0),seq=0,stopped=false,connected=false;
  const pending=new Map();
  const fail=(code,stage)=>{
    if(stopped)return;stopped=true;clearTimeout(timer);
    for(const entry of pending.values())entry.reject(fault(code,stage??entry.stage,
      {requestId:entry.id,tool:entry.tool,requestState:connected?'sent':'not-sent',deliveryState:connected?'unknown':'not-sent'}));
    pending.clear();socket.destroy();
  };
  const timer=setTimeout(()=>fail('DESKTOP_TIMEOUT'),timeoutMs);
  socket.on('connect',()=>{connected=true;});
  socket.on('error',e=>fail(e.code==='ENOENT'?'DESKTOP_PIPE_MISSING':classify(e.code,connected?'CONNECTION_CLOSED':'CONNECTION_FAILED'),connected?undefined:'connect'));
  socket.on('close',()=>fail('CONNECTION_CLOSED'));
  const write=message=>{
    const payload=Buffer.from(JSON.stringify(message)),header=Buffer.alloc(4);
    if(payload.length>MAX_FRAME)throw fault('PROTOCOL_INVALID','request');
    header.writeUInt32LE(payload.length);socket.write(Buffer.concat([header,payload]));
  };
  socket.on('data',chunk=>{
    buf=Buffer.concat([buf,chunk]);
    while(buf.length>=4){
      const n=buf.readUInt32LE();
      if(n>MAX_FRAME||n===0)return fail('PROTOCOL_INVALID','parse');
      if(buf.length<n+4)return;
      let response;try{response=JSON.parse(buf.subarray(4,n+4).toString('utf8'));}
      catch{return fail('PROTOCOL_INVALID','parse');}
      buf=buf.subarray(n+4);
      if(ipc&&response?.type==='client-discovery-request'){
        write({type:'client-discovery-response',requestId:response.requestId,response:{canHandle:false}});continue;
      }
      if(ipc&&response?.type==='broadcast')continue;
      if(!response||(ipc?response.type!=='response':response.jsonrpc!=='2.0'))return fail('PROTOCOL_INVALID','parse');
      const entry=pending.get(ipc?response.requestId:response.id);if(!entry)continue;
      pending.delete(entry.id);
      if(response.error||(ipc&&response.resultType!=='success'))entry.reject(remoteFault(response.error??response,'request',
        {requestId:entry.id,tool:entry.tool}));
      else entry.resolve(ipc?response:response.result);
    }
  });
  return {
    request(method,params,route={}){
      if(stopped)return Promise.reject(fault('CONNECTION_CLOSED','connect'));
      const id=ipc?randomUUID():++seq;
      return new Promise((resolve,reject)=>{
        pending.set(id,{resolve,reject,id,stage:method==='tools/list'?'handshake':'request',tool:params?.tool??method});
        try{write(ipc?{type:'request',requestId:id,method,params,...route}:{id,jsonrpc:'2.0',method,params});}
        catch(e){pending.delete(id);reject(e);}
      });
    },
    close(){fail('CONNECTION_CLOSED');}
  };
}
module.exports={connect};
