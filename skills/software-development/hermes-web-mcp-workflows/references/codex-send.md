# Codex 傳訊

只依人類要求傳訊、續跑或停止確切對話；操作前顯示目標名稱、ID 與訊息。名稱不唯一先釐清；not-sent 不自行換目標。

逐字核對同一筆查詢的 thread_id 與完整 expected_title；send／resume 使用唯一 operation_id。
相同操作沿用 operation_id，以 codex_delivery_status 查回執；結果不明不換 ID 重送。
accepted 表示受理，以 codex_status/read 對照 turnBaseline 查證後續，不將「有新 turn」直接當作此訊息送達的證明。

傳訊前使用最新完成 turn 的真正 completedAt 判斷 30 分鐘閒置；notLoaded、缺少時間或矛盾狀態都回報未派送。
使用者要求傳訊後，閒置對話用 codex_prepare_send(thread_id,expected_title,message,operation_id) 啟動自動流程，不額外提醒或詢問是否 Compact。
最新完成 turn 非 Compact、目前上下文 >80000 tokens 且完成時間距今 >30 分鐘，三者都成立才先壓縮；否則直接傳。用量來自原生 last_token_usage，未知不猜測。閒置對話的發訊或續跑意圖都優先走 prepare_send，不另呼叫 send／resume。
啟動後以唯讀 codex_operation_status(operation_id) 等背景工作。需要壓縮時，只有新的原生 contextCompaction turn 成功完成，才由背景傳送原訊息；不要另呼叫 codex_send。
先讀 summary：compact=not_required 表示本次流程跳過壓縮，compact_complete=false 此時不代表仍在等待；delivery=accepted 表示受理。需要後續 turn 狀態時用 codex_operation_status(observe_turn=true)，turn_observation 只證明 baseline 之後有新 turn，不證明由本訊息觸發，也不代表使用者任務已完成。需要獨立送達回執時，再用 delivery_operation_id 查 codex_delivery_status。逾時、狀態不明、其他 turn 介入或 MCP 重啟均不自動重送；重新檢查並由人類重新要求傳訊。
查詢失敗依 stage/errorCode/requestId/suggestedAction 排錯，不以舊資料庫快照或 generic exception 推論 idle。
