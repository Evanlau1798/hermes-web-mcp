# 即時查詢與操作入口

在已部署的 Web MCP repo 根目錄執行；以下為 PowerShell 寫法。Node.js 需在 PATH，不需要 pip/npm install。先讀 repo 內 `codex-status/README.md`，以本機 `runtime/codex-context.json` 設定 `HERMES_CODEX_CALLER_THREAD_ID`；send/resume 使用部署時設定的 `HERMES_MCP_PYTHON`。

```powershell
node .\codex-status\cli.cjs list
node .\codex-status\cli.cjs status '<thread UUID>'
node .\codex-status\cli.cjs read '<thread UUID>'
node .\codex-status\cli.cjs send '<thread UUID>' '.\tmp\codex-message.txt' '<operation UUID>' '<完整標題>'
node .\codex-status\cli.cjs resume '<thread UUID>' '<operation UUID>' '<完整標題>'
node .\codex-status\cli.cjs stop '<thread UUID>'
```

- list：最近 10 個非釘選對話與釘選對話的標題、ID、狀態、host/cwd。只列 Codex，不列 ChatGPT 聊天；不輸出對話摘要。名單不代表全部對話，找更舊對話先用本機索引取得 ID。
- status：依 execution_backend 取得指定本機 thread 的 Desktop／既有 daemon 即時 status、最近一輪狀態與時間；不輸出訊息或工具命令。指定 ID 可不在最近名單內；讀取不隱含載入。
- read：相同即時狀態，加最近兩輪公開 user/assistant 訊息，每輪最多 12 則、每則最多 1000 字。截斷前會遮蔽常見 API/GitHub/Discord token、JWT、Bearer/Basic、具名密碼/金鑰及 PEM 私鑰；未知格式不能保證辨識，只查狀態時使用 status。保留 page cursor 作資料完整性提示；CLI 尚未開放歷史翻頁，需更舊內容用本機唯讀方式。
- `source=codex-desktop-live` 與 observedAt 表示查詢時間。observedAt 是 UTC，對使用者換算 Asia/Taipei。等待原因只解釋回覆中的 activeFlags；沒有旗標時不要猜。
- read 的 turns[].subagents 會保留近期輪次中的子代理 ID；直接對該 ID 使用同一個 read/status，無須額外接口。
- send：僅在使用者授權該目標及訊息時呼叫。檔案為 UTF-8（最多 64 KiB），中文、換行、$() 及引號以原文傳入，不拼成 shell 命令。
- resume：傳送依既有目標續跑的訊息，可加第三參數指定訊息檔。它不開新對話，也不覆蓋模型／推理設定。
- stop：走實際持有端的 user-stop；Desktop 使用 owner IPC version 3，daemon 使用 Goal 暫停、指定 turn 中斷與該 thread 背景清理。兩端皆未載入則不為停止而載入。後查狀態確認是否已停止，部分成功不報完全停止。不要用 kill/process 重啟或傳「請停止」文字替代。

需 Codex 桌面程式保持開啟。每次動態尋找 codex-app-tools-UUID 管道，不把本次管道 UUID 寫死。沒有或有多條候選、能力缺少、逾時、斷線都會非零退出，不能回報 idle；每個連線階段最多 15 秒，stop 含前查、控制及後查最多約 45 秒。多條候選時需先辨識桌面實例，不任選或重啟。

傳訊走官方 App Tools；停止走同一桌面程式的 codex-ipc owner 路由，先 initialize、thread-owner-discovery，再 thread-follower-interrupt-turn，不自行啟動 router。這是已安裝桌面程式的內部介面，更新後需確認相容性。

操作結果：accepted=已接受但非工作完成；partial=中斷流程部分成功（例如 Goal 暫停失敗）；unknown=送出後回覆不明，可能已執行，禁止自動重試。stopConfirmed=false 時不能報完全停止。退出碼 1 為失敗，2 為部分成功或停止後尚未確認；讀取 JSON 後再判斷，不因非零就重送。

入口查證 Desktop 與既有 daemon 的持有狀態，不把單一後端的 notLoaded 視為整段對話未執行。Desktop 使用官方 codex-app-tools；daemon 使用官方 app-server proxy。Desktop 呼叫來源使用本機設定的既有閒置 routing context，它不是查詢目標；daemon client 身分獨立，不冒用安裝來源 session。沒有讀取登入憑證，也未公開任意 RPC 或複製整套桌面工具。

這是本機已驗證、未公開承諾穩定的內部管道；桌面更新可能改變協定。連線失敗需回報未確認，不能退回舊腳本的「幾分鐘沒輸出就是停止」判斷。status 為當次快照，不是常駐事件訂閱。持續追蹤沿用 thread ID，每 30–60 秒重新 status，達到約定期限或完成條件即停止。

Web MCP 的工具入口為 repo 內 `server.py`，不經 Discord、不啟動 Hermes 模型、不載入人設。它透過 `callerThreadId` 使用本機設定的既有閒置對話作為桌面工具路由資訊；不代表該來源正在執行。路由背景本身不代表對話有新活動。Web MCP 的 `hermes-web-*` 程序命名空間才是工具執行來源，不能把桌面路由來源 ID 當成 Web session 或活動證據。

Web 的 send/resume/stop 必須帶入 `thread_id` 與 `expected_title`，逐字複製同一筆查詢結果；不要重組或混合 UUID。send/resume 另帶唯一 operation_id；CLI 也經同一回執流程，舊的無 ID 形式會拒絕執行。重複 ID 只查回原紀錄，messageContentVerified=false，並未派送本次內容。名稱不符回 TARGET_TITLE_MISMATCH；讀取失敗保留具體 errorCode/stage，不猜目標、不自動重送。持續追蹤使用 hermes_tracking 與唯讀 hermes_tracking_status，操作前讀 hermes-web-mcp-workflows。
