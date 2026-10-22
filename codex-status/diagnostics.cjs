const {randomUUID, createHash} = require('node:crypto');
const reasons = {
  AUTHENTICATION_REQUIRED: ['Codex 登入憑證無法刷新或已失效；查詢／操作未完成。', '在實際持有端確認登入帳號；不要由 Hermes 自動登入、刷新或重送。'],
  BACKEND_WRITER_CONFLICT: ['Codex 回報此對話由其他應用程式持有。', '查證實際持有端；不要另啟 CLI、搶鎖或以另一後端重送。'],
  THREAD_HISTORY_UNAVAILABLE: ['Desktop 未能完成對話歷史彙整；即時讀取未確認。', '保留 requestId 排查原生 history_fetch_failed；歷史快照不可當成即時狀態。'],
  NATIVE_PAGINATION_LIMIT: ['要求的 offset 超出 Desktop 可讀取的有界快照；不代表找不到對話。', '縮小查詢 offset；已知 thread_id 時直接 status/read。Desktop 無 cursor，勿重試相同深頁。'],
  NATIVE_ARGUMENTS_INVALID: ['Desktop 拒絕工具參數；本次未完成查詢。', '依 Desktop 工具 schema 調整參數；保留 requestId，不重試相同參數。'],
  TARGET_CHANGED: ['指定 session 的狀態或最新 turn 已改變，未派送。', '重新查詢並讓使用者確認後，再準備新的操作。'],
  COMPACT_CONFIRMATION_REQUIRED: ['此底層送訊需改走背景自動流程，未傳訊。', '呼叫 codex_prepare_send，依目前上下文與閒置時間自動決定是否先 Compact，不另詢問。'],
  THREAD_STATE_UNCONFIRMED: ['無法確認目標已載入且閒置；未傳送訊息。', '在 Desktop 載入對話後重新查證，notLoaded 不視為 idle。'],
  COMPLETION_TIME_UNCONFIRMED: ['缺少最新 turn 的真正 completedAt，無法套用閒置壓縮規則；未傳送訊息。', '確認 Desktop read_thread 是否提供 completedAt；不可用最後文字或檔案時間代替。'],
  COMPACT_UNCONFIRMED: ['需要真正的 Compact，但目前無法查證壓縮已成功；未傳送訊息。', '檢查原生壓縮狀態，不要以摘要或 /compact 訊息替代。'],
  DESKTOP_PIPE_MISSING: ['找不到 Codex Desktop 工具 pipe；無法確認 Desktop 是否開啟。', '開啟並登入 Codex Desktop，再重新查詢。'],
  DESKTOP_PIPE_AMBIGUOUS: ['找到多個 Desktop pipe，無法安全選擇。', '確認執行中的 Desktop 實例；不要猜測目標。'],
  PERMISSION_DENIED: ['系統拒絕存取 Desktop 連線。', '確認兩端使用同一 Windows 帳號與相容的權限層級。'],
  CONNECTION_FAILED: ['Desktop 連線建立失敗。', '確認 Desktop 已開啟、登入且未在重新啟動。'],
  CONNECTION_CLOSED: ['Desktop 連線已中斷，結果未確認。', '重新查詢；寫入結果不明時先查證，不要重送。'],
  DESKTOP_TIMEOUT: ['Desktop 未在期限內回覆；不能判定 session 閒置。', '稍後重新查詢；不要自動重送寫入。'],
  PROTOCOL_INVALID: ['Desktop 回傳的協定或資料格式無效。', '確認 Desktop 版本與橋接相容，保留 requestId 供排查。'],
  CAPABILITY_UNAVAILABLE: ['Desktop 未提供所需工具或協定能力。', '確認 Desktop 版本及目前可用工具。'],
  THREAD_NOT_LOADED: ['Desktop 回報 thread notLoaded；不代表 session 閒置或已完成。', '在 Desktop 確認此對話的實際載入與工作狀態。'],
  THREAD_NOT_FOUND: ['Desktop 找不到指定對話。', '用 codex_list 重新核對完整 ID 與名稱。'],
  BRIDGE_BUSY: ['Desktop 橋接忙碌，本次查詢未完成。', '稍後重新查詢；勿重送結果不明的寫入。'],
  DESKTOP_REJECTED: ['Desktop 明確回報操作失敗，但未提供可安全揭露的具體原因。', '保留 requestId 與錯誤碼，檢查 Desktop 狀態。'],
};
function classify(value, fallback='DESKTOP_REJECTED') {
  const s=String(value ?? '');
  if (/access token could not be refreshed|refresh_token_(?:reused|expired)|logged out or signed in to another account|authentication required/i.test(s)) return 'AUTHENTICATION_REQUIRED';
  if (/active.writer|open in another app/i.test(s)) return 'BACKEND_WRITER_CONFLICT';
  if (/history_fetch_failed|failed to (?:read|fetch|build).{0,40}history/i.test(s)) return 'THREAD_HISTORY_UNAVAILABLE';
  if (/invalid.{0,24}(?:argument|parameter|limit)|(?:argument|parameter).{0,24}(?:invalid|validation)|too_big|maximum.*limit/i.test(s)) return 'NATIVE_ARGUMENTS_INVALID';
  if (/method.?not.?found|unsupported|incompatible|request-version-mismatch/i.test(s)) return 'CAPABILITY_UNAVAILABLE';
  if (/not.?loaded/i.test(s)) return 'THREAD_NOT_LOADED';
  if (/not.?found|does not exist|no (?:codex )?(?:thread|conversation|session|rollout) found|unable to find (?:thread|conversation)/i.test(s)) return 'THREAD_NOT_FOUND';
  if (/bridge_busy|\bbusy\b/i.test(s)) return 'BRIDGE_BUSY';
  if (/EACCES|EPERM|permission|access denied/i.test(s)) return 'PERMISSION_DENIED';
  return fallback;
}
function fault(code, stage, details={}) {
  const [message, suggestedAction]=reasons[code] ?? [code, '保留 requestId 並查證狀態。'];
  return Object.assign(new Error(message), {code,stage,source:'codex-desktop-live',
    observedAt:new Date().toISOString(),requestId:`hermes-${randomUUID()}`,
    timedOut:code==='DESKTOP_TIMEOUT',disconnected:['CONNECTION_FAILED','CONNECTION_CLOSED'].includes(code),
    automaticRetrySafe:false,suggestedAction,...details});
}
function remoteFault(value, stage, details={}) {
  const raw=JSON.stringify(value ?? {});
  // Raw tool errors may contain conversation text, paths or credentials; retain only classified context.
  const code=value?.code===-32601?'CAPABILITY_UNAVAILABLE':classify(raw);
  const nativeCode=value?.code ?? value?.error?.code;
  return fault(code,stage,{requestState:'rejected',deliveryState:'failed',
    ...(/logged out or signed in to another account/i.test(raw)?{reasonCode:'AUTH_ACCOUNT_CHANGED'}:{}),
    causeFingerprint:createHash('sha256').update(raw).digest('hex').slice(0,16),
    ...(typeof nativeCode==='number' || typeof nativeCode==='string' && /^[A-Z][A-Z0-9_]{1,48}$/.test(nativeCode)?{nativeCode}:{}),...details});
}
function envelope(e, defaults={}) {
  const keys=['code','stage','source','observedAt','requestId','tool','nativeCode','causeFingerprint','reasonCode',
    'timedOut','disconnected','automaticRetrySafe','retryable','suggestedAction','requestState','deliveryState','threadId','nativeLimit','accessibleCount'];
  const result={ok:false,...defaults,...Object.fromEntries(keys.filter(k=>e[k]!==undefined).map(k=>[k,e[k]]))};
  result.errorCode=result.code ?? 'BRIDGE_ERROR';
  result.error=reasons[e.code]?.[0] ?? defaults.error ?? '橋接未能完成操作；具體原因不明。';
  return result;
}
module.exports={fault,remoteFault,envelope,classify};
