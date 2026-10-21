# 持續追蹤

使用者要求持續追蹤時，以 hermes_tracking(action=start) 啟動真正背景 worker，取得 tracking_id 後再表示追蹤已開始。
Codex 用 kind=codex、thread_id；Computer Use 用 kind=computer、由 list_windows 取得的精確 pid + window_id，背景讀取 AX。
Codex 首次查證已載入的持有端後，固定該 backend／instance 取樣；來源消失或重啟時回報未知，不自動換後端判定完成。
mode=manual 追蹤至使用者停止；mode=task_complete 依指定／觀測到的 turn 完成，或明確的電腦文字條件自動停止。可設 deadline_seconds。
電腦條件例如 completion_predicate={"path":["elements",0,"label"],"contains":"完成"}，先擷取並核對欄位，不能臆測完成條件。

start 最多等待 25 秒取得 baseline；以 baseline_ready 判斷有無首份有效狀態。以 hermes_tracking_status(tracking_id,after_sequence,wait_seconds=25) 查詢存活、心跳、current_state、degraded、錯誤與增量事件。
要求持續觀察時，在目前 Web 回合沿同一 tracking_id 和 sequence 接續等待；先採 120 秒觀察預算，使用者指定期間則依其要求。每次檢查 events、wait.reason、alive 與 terminal，重要變化才回報，單純心跳不重複敘述。timeout 只結束本次等待；manual worker 仍存活。達到回合預算時交代最後觀測時間、背景狀態與 tracking_id，不能把背景取樣說成仍有 Web Agent 監看或會自動語音通知。使用 UTC 時間戳計算先後，面向使用者以 Asia/Taipei 顯示，不以 UI 相對時間推算精確時刻。
同樣畫面不重複產生變化事件，missed_events 提示已淘汰的事件。畫面變化後按任務另行擷取，事件不保存私訊文字或圖片。
結束用 hermes_tracking(action=stop,tracking_id=...)；仍有取樣未返回時依 stopping/alive 查到 terminal，不能提前宣稱已完全停止。

背景 worker 可供同一 MCP 程序中的其他 Web 對話接續查詢；重啟後需重新開始。此機制不建立定時排程、不主動通知 Web／Discord，也不能讓已結束的 Web agent 自行醒來。
單次查詢、單張截圖或 wait 後結束都不算持續追蹤。Discord 背景快捷鍵限制依該應用支援處理，不以 focus 或前景操作擅自繞過。
