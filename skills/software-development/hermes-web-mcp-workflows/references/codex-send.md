# Codex 傳訊

只依人類要求傳訊、續跑或停止確切對話。由同筆最新 status/read 逐字核對 thread_id 與完整 expected_title；名稱不唯一先釐清。

推薦 codex_send(thread_id,expected_title,message,operation_id)。prepare_send、resume 是相容入口，共用同一流程與 operation_id 去重；resume 可省略 message 使用預設續跑文字。不要為同步／背景自行切換入口。

依 execution_backend 自動使用 Desktop 或既有 daemon。active 交原生傳訊（daemon 使用帶 expectedTurnId 的 steer），不先 Compact 或停止。idle 只有最新完成 turn 非 Compact、目前上下文 >80000 tokens 且完成時間距今 >30 分鐘三者成立才先壓縮；否則直接傳。不額外詢問是否壓縮。用量未知不猜測；baseline 或狀態不可靠回報未派送。

notLoaded 只表示該後端未載入；傳訊入口可由既有 daemon 背景載入原 ID，重新核對設定與 baseline 再傳送。status/read 不隱含載入。沿同一 operation_id 等待 loading；不要另呼叫 CLI resume、切換前景頁或另啟 app-server。

先讀匹配本次目標與 operation_id 的最新回執 summary.delivery。accepted 只表示受理；耗時工作沿 codex_operation_status(operation_id) 唯讀等待，必要時 observe_turn=true。有 native_turn_id 時依該 turn 查證；只有 baseline 時，新 turn 僅證明較 baseline 新，不保證由此訊息觸發。需要 Compact 時確認新的原生 contextCompaction turn 完成後才由背景傳原訊息，不另呼叫 send。

codex_delivery_status 同時接受公開 operation_id 與回傳的 delivery_operation_id。重複 ID 只回原紀錄，messageContentVerified=false；不同訊息不可因結果不明換 ID 重送。前一次拒絕、後一次新操作受理是不同紀錄，不能用舊拒絕結果覆蓋新回執。MCP 重啟不恢復 RAM 中訊息；結果不明先 status/read 查證，保留原 ID。

依 errorCode/stage/requestId 排錯；不同來源紀錄衝突不猜測。不要以舊資料庫快照推論 idle。手動 confirm_send 僅依人類明確選擇，保留原核准聲明。

同一操作固定後端及實例；writer 衝突、daemon 重啟或結果不明時不跨後端重送。stopConfirmed=false 不報完全停止；CLI 退出／取消訂閱不代表 daemon 立即釋放 writer。完成後原生保留期內，Desktop 仍可能顯示 notLoaded。
停止後可依有可靠 completedAt 的 interrupted／failed 終止 baseline 傳送新指示；它們不算 Compact 完成，也不會因此解除暫停的 Goal。跨程序查回執時，loading／preparing 的 owner 仍存活或無法確認，就維持待查證；只有查證 owner 已退出才回未派送，不換 ID 重播舊訊息。
