# Codex 傳訊

只依人類要求傳訊、續跑或停止確切對話。由同筆最新 status/read 逐字核對 thread_id 與完整 expected_title；名稱不唯一先釐清。

推薦 codex_send(thread_id,expected_title,message,operation_id)。prepare_send、resume 是相容入口，共用同一流程與 operation_id 去重；resume 可省略 message 使用預設續跑文字。不要為同步／背景自行切換入口。

active 直接交 Desktop 原生傳訊及排隊，不先 Compact 或停止。idle 只有最新完成 turn 非 Compact、目前上下文 >80000 tokens 且完成時間距今 >30 分鐘三者成立才先壓縮；否則直接傳。不額外詢問是否壓縮。用量未知不猜測；baseline 或狀態不可靠回報未派送。

notLoaded 需要同一 Desktop 的背景載入介面；目前目錄沒有已驗證能力，回 BACKGROUND_LOAD_UNAVAILABLE、not-sent。不要切換前景頁、另啟 app-server 或替代對話。

先讀匹配本次目標與 operation_id 的最新回執 summary.delivery。accepted 只表示受理；耗時工作沿 codex_operation_status(operation_id) 唯讀等待，必要時 observe_turn=true。新 turn 只證明較 baseline 新，不保證由此訊息觸發，也不等於任務完成。需要 Compact 時確認新的原生 contextCompaction turn 完成後才由背景傳原訊息，不另呼叫 send。

codex_delivery_status 同時接受公開 operation_id 與回傳的 delivery_operation_id。重複 ID 只回原紀錄，messageContentVerified=false；不同訊息不可因結果不明換 ID 重送。前一次拒絕、後一次新操作受理是不同紀錄，不能用舊拒絕結果覆蓋新回執。MCP 重啟不恢復 RAM 中訊息；結果不明先 status/read 查證，保留原 ID。

依 errorCode/stage/requestId 排錯；不同來源紀錄衝突不猜測。不要以舊資料庫快照推論 idle。手動 confirm_send 僅依人類明確選擇，保留原核准聲明。
