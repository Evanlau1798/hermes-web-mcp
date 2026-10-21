const {fault}=require('./diagnostics.cjs');
function preflight(data,now=Date.now()){
  const state=data?.thread?.status?.type,turn=data?.turns?.[0];
  if(state==='active')return {required:false,reason:'active-turn'};
  if(state!=='idle'||turn?.status==='inProgress')throw fault('THREAD_STATE_UNCONFIRMED','lookup');
  const value=turn?.completedAt;
  const completed=typeof value==='number'&&Number.isFinite(value)?value*(value<1e12?1000:1):
    typeof value==='string'&&/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)?Date.parse(value):NaN;
  if(!turn?.id||!['completed','interrupted','failed'].includes(turn.status)||!Number.isFinite(completed)||completed<=0||completed>now+5000)
    throw fault('COMPLETION_TIME_UNCONFIRMED','lookup');
  return {required:false,baselineTurnId:turn.id,completedAt:value};
}
function describe(data){
  const turn=data?.turns?.[0],items=turn?.items;
  const known=Array.isArray(items);
  return {turnId:turn?.id??null,completedAt:turn?.completedAt??null,
    lastTurnIsCompaction:known?turn.status==='completed'&&items.length>0&&items.every(i=>i?.type==='contextCompaction'):null,
    nativeCompactionItemSeen:known?items.some(i=>i?.type==='contextCompaction'):null};
}
module.exports={preflight,describe};
