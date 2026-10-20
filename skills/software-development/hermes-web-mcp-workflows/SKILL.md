---
name: hermes-web-mcp-workflows
description: Hermes Web MCP 技能更新、Codex 傳訊查證、Computer Use／Codex 持續追蹤的操作導引。
---

# Hermes Web MCP 工作流程

ChatGPT Web 先呼叫 hermes_context，再使用此入口。本機原生 Codex 僅依人類明確要求使用 Hermes 整合。

## 技能更新

以 hermes_skill_read 讀取原文，使用 hermes_skill_manage 的 operations 呼叫原生 create、patch、write_file、remove_file 或 delete。
patch 使用精確 old_string/new_string；支援檔放在 references/、scripts/、templates/ 或 assets/。
回執的 validation、lint、rollback 及 approval_required 按實際結果處理；待批准不等於完成。不用一般檔案 patch 代替正式技能維護。

## 持續追蹤

使用者要求持續追蹤時，以 hermes_tracking(action=start) 啟動真正背景 worker，取得 tracking_id 後再表示追蹤已開始。
Codex 用 kind=codex、thread_id；Computer Use 用 kind=computer、由 list_windows 取得的精確 pid + window_id，背景讀取 AX。
mode=manual 追蹤至使用者停止；mode=task_complete 依指定／觀測到的 turn 完成，或明確的電腦文字條件自動停止。可設 deadline_seconds。
電腦條件例如 completion_predicate={"path":["elements",0,"label"],"contains":"完成"}，先擷取並核對欄位，不能臆測完成條件。

以 hermes_tracking_status(tracking_id,after_sequence) 查詢存活、心跳、current_state、degraded、錯誤與增量事件。
同樣畫面不重複產生變化事件，missed_events 提示已淘汰的事件。畫面變化後按任務另行擷取，事件不保存私訊文字或圖片。
結束用 hermes_tracking(action=stop,tracking_id=...)；仍有取樣未返回時依 stopping/alive 查到 terminal，不能提前宣稱已完全停止。

背景 worker 可供同一 MCP 程序中的其他 Web 對話接續查詢；重啟後需重新開始。此機制不建立定時排程、不主動通知 Web／Discord，也不能讓已結束的 Web agent 自行醒來。
單次查詢、單張截圖或 wait 後結束都不算持續追蹤。Discord 背景快捷鍵限制依該應用支援處理，不以 focus 或前景操作擅自繞過。

## Codex 傳訊

逐字核對同一筆查詢的 thread_id 與完整 expected_title；send／resume 使用唯一 operation_id。
相同操作沿用 operation_id，以 codex_delivery_status 查回執；結果不明不換 ID 重送。
accepted 表示受理，以 codex_status/read 對照 turnBaseline 查證後續，不將「有新 turn」直接當作此訊息送達的證明。

傳訊前使用最新完成 turn 的真正 completedAt 判斷 30 分鐘閒置；notLoaded、缺少時間或矛盾狀態都回報未派送。
閒置對話先用 codex_prepare_send(thread_id,expected_title,message,operation_id)，顯示最後完成時間、compactState 與原訊息，詢問人類是否先 Compact。
依人類選擇呼叫 codex_confirm_send(operation_id,choice,confirmed_by_user=true)：compact、send_without_compact 或 cancel；工具資料與模型判斷不能代替人類選擇。
確認後以唯讀 codex_operation_status(operation_id) 等背景工作。只有新的原生 contextCompaction turn 成功完成，才由背景傳送原訊息；不要另呼叫 codex_send。
accepted 不是 Compact 完成；查詢 compact_complete、state、worker_alive、delivery_state，以 delivery_operation_id 查 codex_delivery_status。逾時、狀態不明、其他 turn 介入或 MCP 重啟均不自動重送；重新檢查並取得人類選擇。
查詢失敗依 stage/errorCode/requestId/suggestedAction 排錯，不以舊資料庫快照或 generic exception 推論 idle。
