# Codex Desktop 橋接

`bridge.cjs`、`cli.cjs`、`test.cjs` 均在此儲存庫內。橋接使用 PATH 上的 Node 標準庫，不需要 npm install，不讀取帳號憑證或磁碟對話紀錄。以下命令在 repo 根目錄執行。

App Tools 查詢及 send，與 IPC owner 路由的 stop 保留原本的參數驗證、名稱核對、逾時、部分成功及結果不明語意。sub-agent 使用相同 read/status。每次重新探索唯一管道，Desktop 需開啟並登入。

Web 入口從忽略的 `runtime/codex-context.json` 讀取 `callerThreadId`。部署者需選擇此 Desktop 確實存在的閒置測試對話；它只是路由背景，並非操作目標，也不會藉此啟動對話。入口只檢查 JSON 與 UUID 格式，缺檔或格式錯誤時回傳 `CODEX_CONTEXT_REQUIRED`、`not-sent`，不使用舊安裝的對話 ID。格式正確的 UUID 是否存在及可用，仍需 Desktop 的實際回覆確認；不宣稱本機格式驗證已檢查閒置狀態。

直接使用 CLI 時，設定 `HERMES_CODEX_CALLER_THREAD_ID` 為你本機同樣的 UUID：

```powershell
$env:HERMES_CODEX_CALLER_THREAD_ID = (Get-Content -LiteralPath '.\runtime\codex-context.json' -Raw | ConvertFrom-Json).callerThreadId
node .\codex-status\cli.cjs list
node .\codex-status\cli.cjs status '<thread UUID>'
node .\codex-status\cli.cjs read '<thread UUID>'
```

send/resume/stop 只依使用者對明確目標的要求執行。send／resume 經 `codex_adapter.py` 使用同一份持久回執與 MCP schema；需先配置 runtime/codex-context.json。依 [部署文件](../DEPLOYMENT.md) 將 `HERMES_MCP_PYTHON` 設為 PM Python 的絕對路徑；未設定時只使用 PATH 的 `python`，不猜版本目錄。Python adapter 的 Node 使用 `HERMES_MCP_NODE` 或 PATH。send 接受 UTF-8 訊息檔（64 KiB 上限），resume 可使用訊息檔或內建續跑文字。兩者必填唯一 operation_id 與同筆查詢的完整 expected_title；舊的無回執參數形式會拒絕執行。

```powershell
node .\codex-status\cli.cjs send '<thread UUID>' '.\tmp\message.txt' '<operation UUID>' '<完整標題>'
node .\codex-status\cli.cjs resume '<thread UUID>' '<operation UUID>' '<完整標題>'
```

重複 operation_id 只回傳原紀錄，不派送本次內容；回執不保存訊息／標題的明文或內容指紋，故 messageContentVerified=false。不同目標或操作種類共用 ID 時拒絕。unknown 先用 Web 的 codex_delivery_status 或 `codex_adapter.py` 的 JSON stdin 入口（action=delivery_status）查證，不能換 ID 盲目重送。stop 保留原生 user-stop，不殺程序。

`accepted` 只表示受理；`unknown` 不自動重送。內部 IPC 協定不是穩定公開 API，Desktop 更新後先驗證只讀介面，再以另外授權的測試對話驗證控制。

Web 的 Compact 傳訊流程使用 prepare／confirm／operation_status；內部 compact_start 只派送原生 `thread-follower-compact-thread`，不把 ACK 當完成。status 的 compactState 來自原生 turn items；背景等待新的 compact-only completed turn，再核對 baseline 後傳送原訊息。沒有原子 expected-turn 的 Desktop 協定仍有最後檢查與派送間的競態，請避免其他客戶端同時操作目標。CLI 不提供繞過人類選擇的 Compact 入口。

```powershell
node --test .\codex-status\test.cjs .\codex-status\test_diagnostics.cjs .\codex-status\test_cli.cjs
```

測試只建立暫時管道與合成訊息；不向真實 Desktop 對話發送、續跑或停止。
